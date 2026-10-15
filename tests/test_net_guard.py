import socket

import pytest

from bot.links import LinkError, LinkErrorCode, LinkKind, parse_link
from bot.net_guard import (HopResponse, ResolveError, UnsafeAddressError, ensure_public_host,
                           is_public_ip, resolve_link)


@pytest.mark.parametrize("ip, public", [
    ("8.8.8.8", True),
    ("2a03:2880:f12f:83:face:b00c::25de", True),
    ("127.0.0.1", False), ("10.1.2.3", False), ("172.16.0.1", False), ("192.168.1.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("224.0.0.1", False),
    ("::1", False), ("fe80::1", False), ("fc00::1", False), ("::ffff:127.0.0.1", False),
    ("::ffff:10.0.0.1", False), ("2002:7f00:0001::1", False), ("not-an-ip", False),
])
def test_is_public_ip(ip, public):
    assert is_public_ip(ip) is public


async def allow_all(host, port):
    return None


def fetcher(chain):
    """chain: url -> (status, location)."""
    seen = []

    async def fetch(url):
        seen.append(url)
        status, location = chain[url]
        return HopResponse(status, location)

    fetch.seen = seen
    return fetch


async def test_short_link_resolves_to_post():
    short = parse_link("https://vm.tiktok.com/ZG9Ukw3w2SArh-qIp72/")
    fetch = fetcher({
        "https://vm.tiktok.com/ZG9Ukw3w2SArh-qIp72/":
            (301, "https://www.tiktok.com/@someone/video/7500000000000000000?_r=1&u_code=x"),
    })
    link = await resolve_link(short, fetch, allow_all)
    assert link.kind is LinkKind.TIKTOK_POST
    assert link.url == "https://www.tiktok.com/@someone/video/7500000000000000000"
    assert link.original == "https://vm.tiktok.com/ZG9Ukw3w2SArh-qIp72/"


async def test_redirect_relative_location_and_multiple_hops():
    short = parse_link("https://www.instagram.com/share/reel/BAxyz/")
    fetch = fetcher({
        "https://www.instagram.com/share/reel/BAxyz/": (302, "/share/BAxyz2/"),
        "https://www.instagram.com/share/BAxyz2/": (302, "https://www.instagram.com/reel/Cq1/?igsh=1"),
    })
    link = await resolve_link(short, fetch, allow_all)
    assert link.kind is LinkKind.INSTAGRAM_REEL
    assert len(fetch.seen) == 2


@pytest.mark.parametrize("location", [
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "https://evil.example/@u/video/1",
    "https://www.tiktok.com:8443/@u/video/1",
    "file:///etc/passwd",
    "https://user:pw@www.tiktok.com/@u/video/1",
])
async def test_redirect_to_forbidden_target_is_blocked(location):
    short = parse_link("https://vm.tiktok.com/ZMabc/")
    fetch = fetcher({"https://vm.tiktok.com/ZMabc/": (302, location)})
    with pytest.raises(LinkError) as err:
        await resolve_link(short, fetch, allow_all)
    assert err.value.code in (LinkErrorCode.DOMAIN_NOT_ALLOWED, LinkErrorCode.BAD_SCHEME,
                              LinkErrorCode.BAD_PORT, LinkErrorCode.CREDENTIALS, LinkErrorCode.BAD_HOST)


async def test_redirect_to_homepage_means_dead_link():
    short = parse_link("https://vm.tiktok.com/ZMdead/")
    fetch = fetcher({"https://vm.tiktok.com/ZMdead/": (302, "https://www.tiktok.com/")})
    with pytest.raises(ResolveError) as err:
        await resolve_link(short, fetch, allow_all)
    assert err.value.reason == "dead_link"


async def test_non_redirect_response_is_dead_link():
    short = parse_link("https://vm.tiktok.com/ZMx/")
    with pytest.raises(ResolveError):
        await resolve_link(short, fetcher({"https://vm.tiktok.com/ZMx/": (200, None)}), allow_all)


async def test_redirect_loop_is_limited():
    short = parse_link("https://vm.tiktok.com/ZMloop/")
    fetch = fetcher({
        "https://vm.tiktok.com/ZMloop/": (302, "https://www.tiktok.com/t/ZMloop2/"),
        "https://www.tiktok.com/t/ZMloop2/": (302, "https://vm.tiktok.com/ZMloop/"),
    })
    with pytest.raises(ResolveError) as err:
        await resolve_link(short, fetch, allow_all, max_redirects=4)
    assert err.value.reason == "too_many_redirects"
    assert len(fetch.seen) == 5


async def test_every_hop_host_is_checked():
    checked = []

    async def checker(host, port):
        checked.append(host)
        if host == "www.tiktok.com":
            raise UnsafeAddressError(host)

    short = parse_link("https://vm.tiktok.com/ZMabc/")
    fetch = fetcher({"https://vm.tiktok.com/ZMabc/": (301, "https://www.tiktok.com/@u/video/1")})
    with pytest.raises(UnsafeAddressError):
        await resolve_link(short, fetch, checker)
    assert checked == ["vm.tiktok.com", "www.tiktok.com"]


async def test_dns_pointing_to_private_ip_is_rejected(monkeypatch):
    import asyncio

    async def fake_getaddrinfo(self, host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeAddressError):
        await ensure_public_host("www.tiktok.com")


async def test_dns_public_ip_passes(monkeypatch):
    import asyncio

    async def fake_getaddrinfo(self, host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("23.0.0.1", port))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake_getaddrinfo)
    await ensure_public_host("www.tiktok.com")
