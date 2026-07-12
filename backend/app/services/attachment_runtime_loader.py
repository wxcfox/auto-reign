from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Literal

from app.storage.object_store import (
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
    ObjectStoreUnavailable,
    ObjectTooLarge,
    validate_object_key,
)


_TEXT_MEDIA_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


@dataclass(frozen=True)
class RuntimeAttachmentRef:
    id: str
    filename: str
    media_type: str
    source_object_key: str
    parsed_object_key: str | None
    source_size_bytes: int
    source_content_hash: str
    parsed_size_bytes: int | None
    parsed_content_hash: str | None


@dataclass(frozen=True)
class RuntimeAttachment:
    id: str
    filename: str
    media_type: str
    text: str | None
    image_bytes: bytes | None


class AttachmentRuntimeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _ObjectExpectation:
    key: str
    size_bytes: int
    content_hash: str
    kind: Literal["text", "image"]


class AttachmentRuntimeLoader:
    def __init__(self, *, object_store: ObjectStore) -> None:
        self.object_store = object_store

    def load(self, ref: RuntimeAttachmentRef) -> RuntimeAttachment:
        expectation = self._expectation(ref)
        try:
            stored = self.object_store.get(expectation.key)
        except ObjectTooLarge:
            raise AttachmentRuntimeError("attachment_corrupt") from None
        except (ObjectNotFound, ObjectStoreUnavailable):
            raise AttachmentRuntimeError("attachment_unavailable") from None
        except ObjectStoreError:
            raise AttachmentRuntimeError("attachment_unavailable") from None

        try:
            data = stored.data
            metadata = stored.metadata
            metadata_key = metadata.key
            metadata_size = metadata.size_bytes
        except (AttributeError, TypeError):
            raise AttachmentRuntimeError("attachment_corrupt") from None

        if (
            not isinstance(data, bytes)
            or not isinstance(metadata_key, str)
            or not isinstance(metadata_size, int)
            or isinstance(metadata_size, bool)
            or metadata_key != expectation.key
            or metadata_size != expectation.size_bytes
            or len(data) != expectation.size_bytes
            or hashlib.sha256(data).hexdigest() != expectation.content_hash
        ):
            raise AttachmentRuntimeError("attachment_corrupt")

        if expectation.kind == "image":
            return RuntimeAttachment(
                id=ref.id,
                filename=ref.filename,
                media_type=ref.media_type,
                text=None,
                image_bytes=data,
            )

        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise AttachmentRuntimeError("attachment_corrupt") from None
        if not text.strip():
            raise AttachmentRuntimeError("attachment_corrupt")
        return RuntimeAttachment(
            id=ref.id,
            filename=ref.filename,
            media_type=ref.media_type,
            text=text,
            image_bytes=None,
        )

    def _expectation(self, ref: RuntimeAttachmentRef) -> _ObjectExpectation:
        if ref.media_type in _TEXT_MEDIA_TYPES:
            key = ref.parsed_object_key
            size_bytes = ref.parsed_size_bytes
            content_hash = ref.parsed_content_hash
            kind: Literal["text", "image"] = "text"
        elif ref.media_type in _IMAGE_MEDIA_TYPES:
            key = ref.source_object_key
            size_bytes = ref.source_size_bytes
            content_hash = ref.source_content_hash
            kind = "image"
        else:
            raise AttachmentRuntimeError("attachment_corrupt")

        if (
            not isinstance(key, str)
            or not key
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes <= 0
            or not isinstance(content_hash, str)
            or not content_hash
        ):
            raise AttachmentRuntimeError("attachment_corrupt")
        try:
            validate_object_key(key)
        except ValueError:
            raise AttachmentRuntimeError("attachment_corrupt") from None
        return _ObjectExpectation(
            key=key,
            size_bytes=size_bytes,
            content_hash=content_hash,
            kind=kind,
        )
