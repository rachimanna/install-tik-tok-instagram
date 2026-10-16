"""tools/instagram_session.py: чтение sessionid из базы cookies Firefox."""

import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("instagram_session", ROOT / "tools" / "instagram_session.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def make_profile(root: Path, rows, wal_rows=()):
    db = root / "abc.default-esr" / "cookies.sqlite"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, name TEXT, value TEXT, host TEXT, "
                "lastAccessed INTEGER)")
    con.executemany("INSERT INTO moz_cookies (name, value, host, lastAccessed) VALUES (?, ?, ?, ?)", rows)
    con.commit()
    if wal_rows:
        # Как у открытого Firefox: свежие записи ещё в WAL-файле.
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.executemany("INSERT INTO moz_cookies (name, value, host, lastAccessed) VALUES (?, ?, ?, ?)",
                        wal_rows)
        con.commit()
        return db, con  # соединение не закрываем, чтобы WAL не слился в базу
    con.close()
    return db, None


def test_reads_sessionid_and_ignores_other_sites(tmp_path):
    db, _ = make_profile(tmp_path, [
        ("sessionid", "4242%3Aabc%3A1", ".instagram.com", 1),
        ("ds_user_id", "4242", ".instagram.com", 1),
        ("sessionid", "OTHER", ".example.com", 2),
    ])
    assert tool.find_cookie_dbs([tmp_path]) == [db]
    cookies = tool.read_instagram_cookies(db)
    assert cookies["sessionid"] == "4242%3Aabc%3A1" and "OTHER" not in cookies.values()


def test_reads_fresh_cookies_from_wal(tmp_path):
    db, con = make_profile(tmp_path, [("csrftoken", "t", ".instagram.com", 1)],
                           wal_rows=[("sessionid", "fresh", ".instagram.com", 5)])
    try:
        assert db.with_name("cookies.sqlite-wal").exists()
        assert tool.read_instagram_cookies(db)["sessionid"] == "fresh"
    finally:
        con.close()


def test_output_works_as_instagram_cookies(tmp_path, monkeypatch):
    from bot.config import instagram_cookies_to_netscape
    make_profile(tmp_path, [("sessionid", "4242%3Aabc%3A1", ".instagram.com", 1),
                            ("ds_user_id", "4242", ".instagram.com", 1)])
    out = tmp_path / "INSTAGRAM_COOKIES.txt"
    monkeypatch.setattr(tool, "PROFILE_ROOTS", [tmp_path])
    monkeypatch.setattr(tool, "OUTPUT", out)
    assert tool.main() == 0
    line = out.read_text().strip()
    assert line == "sessionid=4242%3Aabc%3A1; ds_user_id=4242"
    assert "\tsessionid\t4242%3Aabc%3A1" in instagram_cookies_to_netscape(line)
