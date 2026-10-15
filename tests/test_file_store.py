from bot.file_store import FileStore


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, ttl=60, max_bytes=10_000):
    clock = Clock()
    store = FileStore(tmp_path / "cache", ttl, max_bytes, clock=clock)
    store.reset_dir()
    return store, clock


def new_file(tmp_path, name, size=100):
    p = tmp_path / name
    p.write_bytes(b"x" * size)
    return p


def test_put_moves_file_and_get(tmp_path):
    store, _ = make(tmp_path)
    src = new_file(tmp_path, "a.mp4")
    item = store.put([(src, "tiktok_a.mp4")])
    assert not src.exists() and item.parts[0].path.is_file()
    assert store.get(item.token) is item


def test_expired_file_is_deleted(tmp_path):
    store, clock = make(tmp_path)
    item = store.put([(new_file(tmp_path, "a.mp4"), "a.mp4")])
    path = item.parts[0].path
    clock.t += 61
    assert store.get(item.token) is None
    assert not path.exists()


def test_sweep_removes_expired(tmp_path):
    store, clock = make(tmp_path)
    a = store.put([(new_file(tmp_path, "a.mp4"), "a.mp4")])
    clock.t += 30
    b = store.put([(new_file(tmp_path, "b.mp4"), "b.mp4")])
    clock.t += 31
    assert store.sweep() == 1
    assert store.get(a.token) is None and store.get(b.token) is not None


def test_document_file_id_kept_after_local_file_removed(tmp_path):
    store, _ = make(tmp_path)
    item = store.put([(new_file(tmp_path, "a.mp4"), "a.mp4")])
    path = item.parts[0].path
    store.remember_documents(item.token, ["FILE_ID"])
    assert not path.exists()
    got = store.get(item.token)
    assert got.parts[0].document_file_id == "FILE_ID" and got.parts[0].path is None
    assert got.available


def test_group_of_files_under_one_token(tmp_path):
    store, _ = make(tmp_path)
    files = [(new_file(tmp_path, f"{i}.jpg"), f"p{i}.jpg") for i in range(3)]
    item = store.put(files)
    assert [p.filename for p in item.parts] == ["p0.jpg", "p1.jpg", "p2.jpg"]
    store.remember_documents(item.token, ["A", None])
    assert item.parts[0].path is None and item.parts[1].path.is_file()
    store.discard(item.token)
    assert list((tmp_path / "cache").iterdir()) == []


def test_size_limit_evicts_oldest(tmp_path):
    store, clock = make(tmp_path, max_bytes=250)
    a = store.put([(new_file(tmp_path, "a.mp4"), "a.mp4")])
    clock.t += 1
    b = store.put([(new_file(tmp_path, "b.mp4"), "b.mp4")])
    clock.t += 1
    c = store.put([(new_file(tmp_path, "c.mp4"), "c.mp4")])
    assert store.get(a.token) is None
    assert store.get(b.token) and store.get(c.token)
    assert store.total_bytes() <= 250


def test_reset_dir_removes_leftovers(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "old.mp4").write_bytes(b"1")
    store = FileStore(cache, 60, 1000)
    store.reset_dir()
    assert list(cache.iterdir()) == []
