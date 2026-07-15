from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol


class ObjectStoreError(RuntimeError):
    """Base class for stable object-storage failures."""


class ObjectNotFound(ObjectStoreError):
    pass


class ObjectConflict(ObjectStoreError):
    pass


class ObjectStoreUnavailable(ObjectStoreError):
    pass


class ObjectTooLarge(ObjectStoreError):
    pass


@dataclass(frozen=True)
class ObjectMetadata:
    key: str
    etag: str
    size_bytes: int


@dataclass(frozen=True)
class StoredObject:
    data: bytes
    metadata: ObjectMetadata


def validate_put_conditions(*, if_none_match: bool, expected_etag: str | None) -> None:
    if if_none_match and expected_etag is not None:
        raise ValueError("if_none_match and expected_etag are mutually exclusive")


def validate_object_key(key: str, *, allow_prefix: bool = False) -> str:
    if allow_prefix and key == "":
        return ""
    candidate = key[:-1] if allow_prefix and key.endswith("/") else key
    if not candidate or candidate.startswith("/") or "//" in candidate:
        raise ValueError("invalid object key")
    pure = PurePosixPath(candidate)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("invalid object key")
    normalized = pure.as_posix()
    if normalized != candidate:
        raise ValueError("invalid object key")
    return normalized


class ObjectStore(Protocol):
    def put(
        self,
        key: str,
        data: bytes,
        if_none_match: bool = False,
        expected_etag: str | None = None,
    ) -> ObjectMetadata: ...

    def get(self, key: str) -> StoredObject: ...

    def head(self, key: str) -> ObjectMetadata: ...

    def list(self, prefix: str) -> list[ObjectMetadata]: ...

    def delete(self, key: str) -> None:
        """Delete key if present; missing keys are a successful no-op."""
        ...
