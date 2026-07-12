from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib

import pytest

from app.services.attachment_runtime_loader import (
    AttachmentRuntimeError,
    AttachmentRuntimeLoader,
    RuntimeAttachment,
    RuntimeAttachmentRef,
)
from app.storage.object_store import (
    ObjectMetadata,
    ObjectNotFound,
    ObjectStoreError,
    ObjectStoreUnavailable,
    ObjectTooLarge,
    StoredObject,
)
from tests.fake_object_store import FakeObjectStore


TEXT_MEDIA_TYPES = (
    "text/plain",
    "text/markdown",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)
IMAGE_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
SOURCE_KEY = "users/1/attachments/attachment/source.txt"
PARSED_KEY = "users/1/attachments/attachment/parsed.txt"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_ref(
    *,
    data: bytes = b"parsed text",
    media_type: str = "text/plain",
) -> RuntimeAttachmentRef:
    return RuntimeAttachmentRef(
        id="attachment",
        filename="notes.txt",
        media_type=media_type,
        source_object_key=SOURCE_KEY,
        parsed_object_key=PARSED_KEY,
        source_size_bytes=11,
        source_content_hash=_digest(b"source text"),
        parsed_size_bytes=len(data),
        parsed_content_hash=_digest(data),
    )


def _image_ref(
    *,
    data: bytes = b"image bytes",
    media_type: str = "image/png",
) -> RuntimeAttachmentRef:
    return RuntimeAttachmentRef(
        id="attachment",
        filename="diagram.png",
        media_type=media_type,
        source_object_key=SOURCE_KEY,
        parsed_object_key=PARSED_KEY,
        source_size_bytes=len(data),
        source_content_hash=_digest(data),
        parsed_size_bytes=11,
        parsed_content_hash=_digest(b"parsed text"),
    )


class _ReturningStore:
    def __init__(
        self,
        *,
        result: object | None = None,
        error: ObjectStoreError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.get_calls: list[str] = []

    def get(self, key: str):
        self.get_calls.append(key)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.parametrize("media_type", TEXT_MEDIA_TYPES)
def test_text_loads_only_verified_parsed_object(media_type: str) -> None:
    parsed = f"content for {media_type}".encode()
    store = FakeObjectStore()
    store.put(SOURCE_KEY, b"source content")
    store.put(PARSED_KEY, parsed)
    loader = AttachmentRuntimeLoader(object_store=store)

    result = loader.load(_text_ref(data=parsed, media_type=media_type))

    assert result == RuntimeAttachment(
        id="attachment",
        filename="notes.txt",
        media_type=media_type,
        text=parsed.decode(),
        image_bytes=None,
    )
    assert store.get_calls == [PARSED_KEY]


@pytest.mark.parametrize("media_type", IMAGE_MEDIA_TYPES)
def test_image_loads_only_verified_source_object(media_type: str) -> None:
    image = f"bytes for {media_type}".encode()
    store = FakeObjectStore()
    store.put(SOURCE_KEY, image)
    store.put(PARSED_KEY, b"parsed content")
    loader = AttachmentRuntimeLoader(object_store=store)

    result = loader.load(_image_ref(data=image, media_type=media_type))

    assert result == RuntimeAttachment(
        id="attachment",
        filename="diagram.png",
        media_type=media_type,
        text=None,
        image_bytes=image,
    )
    assert store.get_calls == [SOURCE_KEY]


def test_runtime_attachment_values_are_frozen() -> None:
    ref = _text_ref()
    attachment = RuntimeAttachment(
        id=ref.id,
        filename=ref.filename,
        media_type=ref.media_type,
        text="text",
        image_bytes=None,
    )

    with pytest.raises(FrozenInstanceError):
        ref.filename = "changed.txt"
    with pytest.raises(FrozenInstanceError):
        attachment.text = "changed"


@pytest.mark.parametrize(
    "ref",
    [
        replace(_text_ref(), parsed_object_key=None),
        replace(_text_ref(), parsed_object_key="../secret"),
        replace(_text_ref(), parsed_size_bytes=None),
        replace(_text_ref(), parsed_content_hash=None),
        replace(_image_ref(), source_object_key=""),
        replace(_image_ref(), source_size_bytes=0),
        replace(_image_ref(), source_content_hash=""),
    ],
)
def test_missing_or_invalid_expected_metadata_is_corrupt_without_reading(
    ref: RuntimeAttachmentRef,
) -> None:
    store = _ReturningStore()

    with pytest.raises(AttachmentRuntimeError) as captured:
        AttachmentRuntimeLoader(object_store=store).load(ref)  # type: ignore[arg-type]

    assert captured.value.code == "attachment_corrupt"
    assert store.get_calls == []


def test_unknown_media_type_is_corrupt_without_reading() -> None:
    store = _ReturningStore()
    ref = replace(_text_ref(), media_type="application/octet-stream")

    with pytest.raises(AttachmentRuntimeError) as captured:
        AttachmentRuntimeLoader(object_store=store).load(ref)  # type: ignore[arg-type]

    assert captured.value.code == "attachment_corrupt"
    assert store.get_calls == []


@pytest.mark.parametrize(
    "stored",
    [
        object(),
        StoredObject(data=b"parsed text", metadata=None),  # type: ignore[arg-type]
        StoredObject(
            data=b"parsed text",
            metadata=ObjectMetadata(
                key="users/1/attachments/other/parsed.txt",
                etag="opaque",
                size_bytes=11,
            ),
        ),
        StoredObject(
            data=b"parsed text",
            metadata=ObjectMetadata(
                key=PARSED_KEY,
                etag="opaque",
                size_bytes=12,
            ),
        ),
        StoredObject(
            data=b"short",
            metadata=ObjectMetadata(
                key=PARSED_KEY,
                etag="opaque",
                size_bytes=11,
            ),
        ),
    ],
)
def test_missing_or_contradictory_stored_metadata_is_corrupt(stored: object) -> None:
    store = _ReturningStore(result=stored)

    with pytest.raises(AttachmentRuntimeError) as captured:
        AttachmentRuntimeLoader(object_store=store).load(  # type: ignore[arg-type]
            _text_ref()
        )

    assert captured.value.code == "attachment_corrupt"
    assert store.get_calls == [PARSED_KEY]


def test_hash_mismatch_is_corrupt() -> None:
    data = b"parsed text"
    stored = StoredObject(
        data=data,
        metadata=ObjectMetadata(
            key=PARSED_KEY,
            etag="opaque",
            size_bytes=len(data),
        ),
    )
    store = _ReturningStore(result=stored)
    ref = replace(_text_ref(data=data), parsed_content_hash=_digest(b"other text"))

    with pytest.raises(AttachmentRuntimeError) as captured:
        AttachmentRuntimeLoader(object_store=store).load(ref)  # type: ignore[arg-type]

    assert captured.value.code == "attachment_corrupt"


@pytest.mark.parametrize("data", [b"\xff", b"", b" \n\t"])
def test_invalid_utf8_or_empty_text_is_corrupt(data: bytes) -> None:
    stored = StoredObject(
        data=data,
        metadata=ObjectMetadata(
            key=PARSED_KEY,
            etag="opaque",
            size_bytes=len(data),
        ),
    )
    store = _ReturningStore(result=stored)
    ref = _text_ref(data=data)

    with pytest.raises(AttachmentRuntimeError) as captured:
        AttachmentRuntimeLoader(object_store=store).load(ref)  # type: ignore[arg-type]

    assert captured.value.code == "attachment_corrupt"


@pytest.mark.parametrize(
    ("store_error", "code"),
    [
        (ObjectNotFound("secret-key"), "attachment_unavailable"),
        (ObjectStoreUnavailable("secret endpoint"), "attachment_unavailable"),
        (ObjectStoreError("provider secret"), "attachment_unavailable"),
        (ObjectTooLarge("secret-key"), "attachment_corrupt"),
    ],
)
def test_store_errors_map_to_safe_codes(
    store_error: ObjectStoreError,
    code: str,
) -> None:
    store = _ReturningStore(error=store_error)

    with pytest.raises(AttachmentRuntimeError) as captured:
        AttachmentRuntimeLoader(object_store=store).load(  # type: ignore[arg-type]
            _text_ref()
        )

    assert captured.value.code == code
    assert str(captured.value) == code
    assert captured.value.__cause__ is None
    assert "secret" not in str(captured.value)
    assert store.get_calls == [PARSED_KEY]
