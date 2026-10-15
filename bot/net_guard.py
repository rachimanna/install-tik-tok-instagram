"""Защита от SSRF: проверка IP-адресов и безопасное раскрытие коротких ссылок.

Каждый шаг редиректа заново проходит parse_link (разрешённые домены, схема,
порт, отсутствие логина/пароля в URL) и проверку DNS: все адреса хоста должны
быть публичными. Внутри соединения aiohttp дополнительно использует SafeResolver,
чтобы подмена DNS между проверкой и подключением не привела в приватную сеть.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import ThreadedResolver

from .links import LinkError, LinkErrorCode, ParsedLink, parse_link
from .redact import redact_url

log = logging.getLogger(__name__)

MAX_REDIRECTS = 6
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


class UnsafeAddressError(Exception):
    """Хост указывает на приватный/служебный адрес."""


class ResolveError(Exception):
    """Не удалось раскрыть короткую ссылку."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # "network" | "dead_link" | "too_many_redirects"


def is_public_ip(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        ip = ipaddress.ip_address(value) if isinstance(value, str) else value
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None and not is_public_ip(mapped):
            return False
        if ip.teredo is not None:
            return False
    return bool(
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


async def ensure_public_host(host: str, port: int = 443) -> None:
    """Все адреса, в которые разрешается host, должны быть публичными."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not is_public_ip(host):
            raise UnsafeAddressError(host)
        return

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ResolveError("network") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise ResolveError("network")
    bad = [a for a in addresses if not is_public_ip(a.split("%", 1)[0])]
    if bad:
        raise UnsafeAddressError(host)


class SafeResolver(AbstractResolver):
    """DNS-резолвер для aiohttp, отбрасывающий приватные и служебные адреса."""

    def __init__(self) -> None:
        self._inner = ThreadedResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[ResolveResult]:
        results = await self._inner.resolve(host, port, family)
        if any(not is_public_ip(r["host"]) for r in results):
            raise OSError(f"Refusing non-public address for {host}")
        return results

    async def close(self) -> None:
        await self._inner.close()


def make_http_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(resolver=SafeResolver(), ttl_dns_cache=60)
    return aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=20, connect=10),
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        trust_env=True,  # уважать HTTPS_PROXY, если он задан на сервере
    )


@dataclass
class HopResponse:
    status: int
    location: str | None


Fetcher = Callable[[str], Awaitable[HopResponse]]
HostChecker = Callable[[str, int], Awaitable[None]]


def aiohttp_fetcher(session: aiohttp.ClientSession) -> Fetcher:
    async def fetch(url: str) -> HopResponse:
        async with session.get(url, allow_redirects=False) as resp:
            return HopResponse(resp.status, resp.headers.get("Location"))

    return fetch


async def resolve_link(
    link: ParsedLink,
    fetch: Fetcher,
    check_host: HostChecker = ensure_public_host,
    max_redirects: int = MAX_REDIRECTS,
) -> ParsedLink:
    """Раскрывает короткую ссылку, проверяя каждый шаг. Возвращает ссылку на контент."""
    current = link
    for _ in range(max_redirects + 1):
        parts = urlsplit(current.url)
        await check_host(parts.hostname or "", parts.port or 443)
        if not current.needs_resolve:
            return ParsedLink(current.platform, current.kind, current.url, link.original, current.content_id)

        try:
            resp = await fetch(current.url)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            log.info("Redirect fetch failed for %s: %s", redact_url(current.url), type(exc).__name__)
            raise ResolveError("network") from exc

        if resp.status not in (301, 302, 303, 307, 308) or not resp.location:
            raise ResolveError("dead_link")

        next_url = urljoin(current.url, resp.location)
        log.debug("Redirect %s -> %s", redact_url(current.url), redact_url(next_url))
        try:
            current = parse_link(next_url, original=link.original)
        except LinkError as exc:
            # Редирект на главную/логин/чужой домен — ссылка недействительна или опасна.
            if exc.code in (LinkErrorCode.DOMAIN_NOT_ALLOWED, LinkErrorCode.BAD_HOST,
                            LinkErrorCode.BAD_SCHEME, LinkErrorCode.BAD_PORT,
                            LinkErrorCode.CREDENTIALS):
                raise
            if exc.code is LinkErrorCode.UNSUPPORTED_CONTENT:
                raise ResolveError("dead_link") from exc
            raise
    raise ResolveError("too_many_redirects")

