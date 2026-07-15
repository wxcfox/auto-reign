from __future__ import annotations

import hashlib
from threading import Lock

from app.storage.object_store import (
    ObjectConflict,
    ObjectMetadata,
    ObjectNotFound,
    ObjectStoreUnavailable,
    StoredObject,
    validate_object_key,
    validate_put_conditions,
)


class FakeObjectStore:
    def __init__(
        self,
        *,
        put_then_raise_on_call: int | None = None,
        delete_error: Exception | None = None,
        get_error: Exception | None = None,
    ) -> None:
        self.put_then_raise_on_call = put_then_raise_on_call
        self.delete_error = delete_error
        self.get_error = get_error
        self.put_calls: list[str] = []
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []
        self._objects: dict[str, bytes] = {}
        self._lock = Lock()

    def put(
        self,
        key: str,
        data: bytes,
        if_none_match: bool = False,
        expected_etag: str | None = None,
    ) -> ObjectMetadata:
        validate_object_key(key)
        validate_put_conditions(
            if_none_match=if_none_match,
            expected_etag=expected_etag,
        )
        with self._lock:
            current = self._metadata(key) if key in self._objects else None
            if if_none_match and current is not None:
                raise ObjectConflict(key)
            if expected_etag is not None and (
                current is None or current.etag != expected_etag
            ):
                raise ObjectConflict(key)
            self.put_calls.append(key)
            self._objects[key] = bytes(data)
            metadata = self._metadata(key)
            if self.put_then_raise_on_call == len(self.put_calls):
                raise ObjectStoreUnavailable("uncertain put")
            return metadata

    def get(self, key: str) -> StoredObject:
        validate_object_key(key)
        self.get_calls.append(key)
        if self.get_error is not None:
            raise self.get_error
        with self._lock:
            try:
                data = self._objects[key]
            except KeyError as exc:
                raise ObjectNotFound(key) from exc
            return StoredObject(data=data, metadata=self._metadata(key))

    def head(self, key: str) -> ObjectMetadata:
        validate_object_key(key)
        with self._lock:
            if key not in self._objects:
                raise ObjectNotFound(key)
            return self._metadata(key)

    def list(self, prefix: str) -> list[ObjectMetadata]:
        normalized = validate_object_key(prefix, allow_prefix=True)
        match_prefix = f"{normalized}/" if normalized else ""
        with self._lock:
            return [
                self._metadata(key)
                for key in sorted(self._objects)
                if key == normalized or key.startswith(match_prefix)
            ]

    def delete(self, key: str) -> None:
        validate_object_key(key)
        self.delete_calls.append(key)
        if self.delete_error is not None:
            raise self.delete_error
        with self._lock:
            self._objects.pop(key, None)

    def keys(self) -> list[str]:
        with self._lock:
            return sorted(self._objects)

    def replace(self, key: str, data: bytes) -> None:
        validate_object_key(key)
        with self._lock:
            if key not in self._objects:
                raise ObjectNotFound(key)
            self._objects[key] = bytes(data)

    def _metadata(self, key: str) -> ObjectMetadata:
        data = self._objects[key]
        return ObjectMetadata(
            key=key,
            etag=f"sha256:{hashlib.sha256(data).hexdigest()}",
            size_bytes=len(data),
        )
