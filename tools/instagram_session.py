"""Достаёт cookie sessionid Instagram из Firefox и сохраняет строку для INSTAGRAM_COOKIES.

Запуск (после входа в Instagram в Firefox):  python3 tools/instagram_session.py
Результат: файл INSTAGRAM_COOKIES.txt в корне репозитория — его содержимое вставьте
в Render → Environment → INSTAGRAM_COOKIES. Файл в .gitignore, в git не попадёт.

Firefox хранит cookies в SQLite-базе профиля (cookies.sqlite, таблица moz_cookies).
Пока Firefox открыт, свежие записи могут лежать в cookies.sqlite-wal, поэтому база
копируется вместе с ним во временную папку и читается оттуда.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

PROFILE_ROOTS = [Path.home() / ".mozilla" / "firefox", Path.home() / "snap/firefox/common/.mozilla/firefox"]
OUTPUT = Path(__file__).resolve().parent.parent / "INSTAGRAM_COOKIES.txt"


def find_cookie_dbs(roots: list[Path]) -> list[Path]:
    dbs = [db for root in roots if root.is_dir() for db in root.glob("*/cookies.sqlite")]
    return sorted(dbs, key=lambda p: p.stat().st_mtime, reverse=True)


def read_instagram_cookies(db: Path) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "cookies.sqlite"
        shutil.copyfile(db, copy)
        for suffix in ("-wal", "-shm"):
            extra = db.with_name(db.name + suffix)
            if extra.exists():
                shutil.copyfile(extra, copy.with_name(copy.name + suffix))
        con = sqlite3.connect(copy)
        try:
            rows = con.execute(
                "SELECT name, value FROM moz_cookies "
                "WHERE host = 'instagram.com' OR host LIKE '%.instagram.com' "
                "ORDER BY lastAccessed").fetchall()
        finally:
            con.close()
    return {name: value for name, value in rows}


def main() -> int:
    dbs = find_cookie_dbs(PROFILE_ROOTS)
    if not dbs:
        print("Не нашёл профиль Firefox. Сначала запустите tools/open-instagram.sh и войдите в Instagram.")
        return 1
    for db in dbs:
        cookies = read_instagram_cookies(db)
        if cookies.get("sessionid"):
            line = f"sessionid={cookies['sessionid']}"
            if cookies.get("ds_user_id"):
                line += f"; ds_user_id={cookies['ds_user_id']}"
            OUTPUT.write_text(line + "\n", encoding="utf-8")
            OUTPUT.chmod(0o600)
            print(f"Готово. Строка сохранена в {OUTPUT.name} — откройте файл слева, скопируйте")
            print("всё содержимое и вставьте в Render → Environment → INSTAGRAM_COOKIES.")
            return 0
    print("В Firefox нет входа в Instagram (cookie sessionid не найдена).")
    print("Войдите на instagram.com в Firefox на рабочем столе и запустите скрипт ещё раз.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
