from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import (
    ObjectConflict,
    ObjectNotFound,
    ObjectStoreUnavailable,
    ObjectTooLarge,
)


def test_local_store_enforces_conditional_writes(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    created = store.put("users/1/a.txt", b"one", if_none_match=True)

    assert store.get(created.key).data == b"one"
    assert store.get(created.key).metadata == created
    with pytest.raises(ObjectConflict):
        store.put(created.key, b"two", if_none_match=True)
    with pytest.raises(ObjectConflict):
        store.put(created.key, b"two", expected_etag="sha256:stale")

    updated = store.put(created.key, b"two", expected_etag=created.etag)
    assert updated.etag != created.etag
    assert [item.key for item in store.list("users/1/")] == ["users/1/a.txt"]

    store.delete(created.key)
    store.delete(created.key)
    with pytest.raises(ObjectNotFound):
        store.get(created.key)


def test_same_key_create_if_absent_is_atomic_within_one_process(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    start = Barrier(2)

    def create(data: bytes) -> str:
        start.wait()
        try:
            store.put("users/1/a.txt", data, if_none_match=True)
            return "created"
        except ObjectConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [b"one", b"two"]))

    assert sorted(results) == ["conflict", "created"]
    assert store.get("users/1/a.txt").data in {b"one", b"two"}


@pytest.mark.parametrize(
    "key",
    ["/etc/passwd", "../escape", "users/1/../../escape", "users//1/file"],
)
def test_local_store_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    with pytest.raises(ValueError, match="object key"):
        store.put(key, b"blocked")


def test_mutations_reads_and_each_list_item_use_the_same_stripe(
    tmp_path: Path,
) -> None:
    class CountingLock:
        def __init__(self) -> None:
            self.entries = 0

        def __enter__(self):
            self.entries += 1

        def __exit__(self, *_args):
            return False

    store = LocalObjectStore(tmp_path / "objects")
    lock = CountingLock()
    store._locks[store._lock_index("users/1/a.txt")] = lock  # type: ignore[assignment]
    stored = store.put("users/1/a.txt", b"one", if_none_match=True)
    store.get(stored.key)
    store.head(stored.key)
    store.list("users/1/")
    store.delete(stored.key)
    store.delete(stored.key)

    assert lock.entries == 6
    for index in range(1_000):
        store._key_lock(f"users/1/{index}.txt")
    assert len(store._locks) == 256


def test_list_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    store = LocalObjectStore(root)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    link = root / "users" / "1" / "escape.txt"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    with pytest.raises(ObjectStoreUnavailable):
        store.list("users/1/")


def test_local_store_rejects_oversized_put_and_external_file_before_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    store = LocalObjectStore(root, max_read_bytes=4)
    with pytest.raises(ObjectTooLarge):
        store.put("users/1/too-large.txt", b"12345")

    path = root / "users" / "1" / "external.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"12345")
    with pytest.raises(ObjectTooLarge):
        store.get("users/1/external.txt")


def test_local_store_lists_from_root_prefix_in_key_order(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    store.put("users/2/b.txt", b"b")
    store.put("users/1/a.txt", b"a")

    assert [item.key for item in store.list("")] == [
        "users/1/a.txt",
        "users/2/b.txt",
    ]
