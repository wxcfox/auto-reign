from __future__ import annotations

import asyncio

import pytest

from app.services.upload_validation_service import (
    SUPPORTED_MIME_TYPES_BY_EXTENSION,
    UploadPolicy,
    UploadValidationError,
    UploadValidationService,
    default_upload_policy,
)


POLICY = UploadPolicy(
    max_bytes=5,
    allowed_mime_types=frozenset({"text/plain"}),
    allowed_extensions=frozenset({".txt"}),
)


class ChunkedUpload:
    def __init__(self, filename: str | None, content_type: str | None, content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.remaining = content
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        count = min(size, 3, len(self.remaining))
        chunk, self.remaining = self.remaining[:count], self.remaining[count:]
        return chunk


def test_upload_reader_is_bounded_and_hashes_valid_content() -> None:
    upload = ChunkedUpload("note.txt", "text/plain", b"hello")

    result = asyncio.run(UploadValidationService().read_required(upload, policy=POLICY))

    assert result.filename == "note.txt"
    assert result.mime_type == "text/plain"
    assert result.content == b"hello"
    assert result.size_bytes == 5
    assert result.content_hash == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    assert all(0 < size <= POLICY.max_bytes + 1 for size in upload.read_sizes)


def test_upload_reader_stops_after_max_plus_one_byte() -> None:
    upload = ChunkedUpload("note.txt", "text/plain", b"123456789")

    with pytest.raises(UploadValidationError, match="size") as captured:
        asyncio.run(UploadValidationService().read_required(upload, policy=POLICY))

    assert captured.value.code == "upload_too_large"
    assert len(upload.remaining) == 3
    assert -1 not in upload.read_sizes


@pytest.mark.parametrize(
    ("filename", "mime_type", "content", "code"),
    [
        (None, "text/plain", b"x", "upload_filename_invalid"),
        ("", "text/plain", b"x", "upload_filename_invalid"),
        ("../note.txt", "text/plain", b"x", "upload_filename_invalid"),
        ("folder/note.txt", "text/plain", b"x", "upload_filename_invalid"),
        (r"folder\note.txt", "text/plain", b"x", "upload_filename_invalid"),
        ("note\x00.txt", "text/plain", b"x", "upload_filename_invalid"),
        ("line\nbreak.txt", "text/plain", b"x", "upload_filename_invalid"),
        ("line\rbreak.txt", "text/plain", b"x", "upload_filename_invalid"),
        ("tab\tname.txt", "text/plain", b"x", "upload_filename_invalid"),
        ("control\x1fname.txt", "text/plain", b"x", "upload_filename_invalid"),
        ("delete\x7fname.txt", "text/plain", b"x", "upload_filename_invalid"),
        (("x" * 252) + ".txt", "text/plain", b"x", "upload_filename_invalid"),
        ("note.pdf", "text/plain", b"x", "upload_type_invalid"),
        ("note.txt", "application/pdf", b"x", "upload_type_invalid"),
        ("note.txt", None, b"x", "upload_type_invalid"),
        ("note.txt", "text/plain", b"", "upload_empty"),
    ],
)
def test_upload_reader_rejects_invalid_input(
    filename: str | None,
    mime_type: str | None,
    content: bytes,
    code: str,
) -> None:
    with pytest.raises(UploadValidationError) as captured:
        asyncio.run(
            UploadValidationService().read_required(
                ChunkedUpload(filename, mime_type, content),
                policy=POLICY,
            )
        )

    assert captured.value.code == code


@pytest.mark.parametrize(
    ("filename", "mime_type"),
    [
        ("README.MD", "TEXT/MARKDOWN; charset=UTF-8"),
        ("note.txt", "text/plain"),
        ("paper.pdf", "application/pdf"),
        (
            "report.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("image.png", "image/png"),
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("image.webp", "image/webp"),
        ("animation.gif", "image/gif"),
    ],
)
def test_supported_extension_and_mime_pairs_are_accepted(
    filename: str,
    mime_type: str,
) -> None:
    normalized_mime = mime_type.partition(";")[0].strip().lower()
    policy = UploadPolicy(
        max_bytes=10,
        allowed_mime_types=frozenset({normalized_mime}),
        allowed_extensions=frozenset({f".{filename.rsplit('.', 1)[1].lower()}"}),
    )

    result = asyncio.run(
        UploadValidationService(chunk_bytes=2).read_required(
            ChunkedUpload(filename, mime_type, b"data"),
            policy=policy,
        )
    )

    assert result.mime_type == normalized_mime
    assert result.filename == filename


def test_policy_cannot_enable_an_unknown_mime_extension_pair() -> None:
    policy = UploadPolicy(
        max_bytes=10,
        allowed_mime_types=frozenset({"application/octet-stream"}),
        allowed_extensions=frozenset({".bin"}),
    )

    with pytest.raises(UploadValidationError) as captured:
        asyncio.run(
            UploadValidationService().read_required(
                ChunkedUpload("payload.bin", "application/octet-stream", b"data"),
                policy=policy,
            )
        )

    assert captured.value.code == "upload_type_invalid"


def test_supported_type_registry_is_immutable_at_each_extension() -> None:
    assert all(isinstance(mime_types, frozenset) for mime_types in SUPPORTED_MIME_TYPES_BY_EXTENSION.values())


def test_default_upload_policy_is_derived_from_the_supported_type_registry() -> None:
    policy = default_upload_policy(max_bytes=123)

    assert policy.max_bytes == 123
    assert policy.allowed_extensions == frozenset(SUPPORTED_MIME_TYPES_BY_EXTENSION)
    assert policy.allowed_mime_types == frozenset(
        mime_type
        for mime_types in SUPPORTED_MIME_TYPES_BY_EXTENSION.values()
        for mime_type in mime_types
    )


def test_upload_reader_requires_positive_chunk_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        UploadValidationService(chunk_bytes=0)
