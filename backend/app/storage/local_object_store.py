from __future__ import annotations

import hashlib
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.core.limits import DEFAULT_OBJECT_STORE_MAX_READ_BYTES
from app.storage.object_store import (
    ObjectConflict,
    ObjectMetadata,
    ObjectNotFound,
    ObjectStoreUnavailable,
    ObjectTooLarge,
    StoredObject,
    validate_object_key,
    validate_put_conditions,
)


class LocalObjectStore:
    """Single-process development store; cross-process locking is unsupported."""

    def __init__(
        self,
        root: Path,
        *,
        max_read_bytes: int = DEFAULT_OBJECT_STORE_MAX_READ_BYTES,
    ) -> None:
        if max_read_bytes <= 0:
            raise ValueError("max_read_bytes must be positive")
        self.root = root.resolve()
        self.max_read_bytes = max_read_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks = [Lock() for _ in range(256)]

    def put(
        self,
        key: str,
        data: bytes,
        if_none_match: bool = False,
        expected_etag: str | None = None,
    ) -> ObjectMetadata:
        validate_put_conditions(
            if_none_match=if_none_match,
            expected_etag=expected_etag,
        )
        if len(data) > self.max_read_bytes:
            raise ObjectTooLarge(key)
        path = self._path(key)
        with self._key_lock(key):
            current = self._metadata(key, path) if path.exists() else None
            if if_none_match and current is not None:
                raise ObjectConflict(key)
            if expected_etag is not None and (
                current is None or current.etag != expected_etag
            ):
                raise ObjectConflict(key)

            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            try:
                with temporary.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            except OSError as exc:
                raise ObjectStoreUnavailable(key) from exc
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return self._metadata(key, path, data=data)

    def get(self, key: str) -> StoredObject:
        path = self._path(key)
        with self._key_lock(key):
            try:
                data = self._read_bounded(key, path)
                return StoredObject(
                    data=data,
                    metadata=self._metadata(key, path, data=data),
                )
            except FileNotFoundError as exc:
                raise ObjectNotFound(key) from exc
            except OSError as exc:
                raise ObjectStoreUnavailable(key) from exc

    def head(self, key: str) -> ObjectMetadata:
        path = self._path(key)
        with self._key_lock(key):
            if not path.is_file():
                raise ObjectNotFound(key)
            return self._metadata(key, path)

    def list(self, prefix: str) -> list[ObjectMetadata]:
        prefix_path = self._path(prefix, allow_prefix=True)
        if not prefix_path.exists():
            return []
        paths = [prefix_path] if prefix_path.is_file() else sorted(prefix_path.rglob("*"))
        items: list[ObjectMetadata] = []
        for path in paths:
            resolved = path.resolve(strict=False)
            if path.is_symlink() or not resolved.is_relative_to(self.root):
                raise ObjectStoreUnavailable("object escaped local root")
            if not path.is_file():
                continue
            key = validate_object_key(path.relative_to(self.root).as_posix())
            with self._key_lock(key):
                items.append(self._metadata(key, path))
        return items

    def delete(self, key: str) -> None:
        path = self._path(key)
        with self._key_lock(key):
            try:
                path.unlink()
            except FileNotFoundError:
                return
            except OSError as exc:
                raise ObjectStoreUnavailable(key) from exc

    def _path(self, key: str, *, allow_prefix: bool = False) -> Path:
        normalized = validate_object_key(key, allow_prefix=allow_prefix)
        resolved = (self.root / Path(*normalized.split("/"))).resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ValueError("invalid object key")
        return resolved

    def _key_lock(self, key: str) -> Lock:
        return self._locks[self._lock_index(key)]

    def _lock_index(self, key: str) -> int:
        digest = hashlib.sha256(validate_object_key(key).encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % len(self._locks)

    def _metadata(
        self,
        key: str,
        path: Path,
        *,
        data: bytes | None = None,
    ) -> ObjectMetadata:
        try:
            data = self._read_bounded(key, path) if data is None else data
            return ObjectMetadata(
                key=key,
                etag=f"sha256:{hashlib.sha256(data).hexdigest()}",
                size_bytes=len(data),
            )
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc
        except OSError as exc:
            raise ObjectStoreUnavailable(key) from exc

    def _read_bounded(self, key: str, path: Path) -> bytes:
        try:
            if path.stat().st_size > self.max_read_bytes:
                raise ObjectTooLarge(key)
            chunks: list[bytes] = []
            received = 0
            with path.open("rb") as stream:
                while received <= self.max_read_bytes:
                    chunk = stream.read(
                        min(64 * 1024, self.max_read_bytes + 1 - received)
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
            if received > self.max_read_bytes:
                raise ObjectTooLarge(key)
            return b"".join(chunks)
        except (ObjectTooLarge, ObjectNotFound, ObjectStoreUnavailable):
            raise
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc
        except OSError as exc:
            raise ObjectStoreUnavailable(key) from exc
