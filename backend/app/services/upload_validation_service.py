from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile


SUPPORTED_MIME_TYPES_BY_EXTENSION: dict[str, frozenset[str]] = {
    ".md": frozenset({"text/markdown", "text/plain"}),
    ".txt": frozenset({"text/plain"}),
    ".pdf": frozenset({"application/pdf"}),
    ".docx": frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    ),
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".webp": frozenset({"image/webp"}),
    ".gif": frozenset({"image/gif"}),
}


@dataclass(frozen=True)
class UploadPolicy:
    max_bytes: int
    allowed_mime_types: frozenset[str]
    allowed_extensions: frozenset[str]


def default_upload_policy(*, max_bytes: int) -> UploadPolicy:
    """Build the single supported attachment policy for the application."""
    return UploadPolicy(
        max_bytes=max_bytes,
        allowed_mime_types=frozenset(
            mime_type
            for mime_types in SUPPORTED_MIME_TYPES_BY_EXTENSION.values()
            for mime_type in mime_types
        ),
        allowed_extensions=frozenset(SUPPORTED_MIME_TYPES_BY_EXTENSION),
    )


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    mime_type: str
    content: bytes
    size_bytes: int
    content_hash: str


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UploadValidationService:
    def __init__(self, *, chunk_bytes: int = 64 * 1024) -> None:
        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be positive")
        self.chunk_bytes = chunk_bytes

    async def read_required(
        self,
        upload: UploadFile,
        *,
        policy: UploadPolicy,
    ) -> ValidatedUpload:
        if policy.max_bytes <= 0:
            raise ValueError("policy.max_bytes must be positive")

        filename = (upload.filename or "").strip()
        if (
            not filename
            or len(filename) > 255
            or "/" in filename
            or "\\" in filename
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in filename)
        ):
            raise UploadValidationError(
                "upload_filename_invalid",
                "upload filename is invalid",
            )

        suffix = Path(filename).suffix.lower()
        mime_type = (upload.content_type or "").partition(";")[0].strip().lower()
        supported_for_suffix = SUPPORTED_MIME_TYPES_BY_EXTENSION.get(suffix, frozenset())
        if (
            suffix not in policy.allowed_extensions
            or mime_type not in policy.allowed_mime_types
            or mime_type not in supported_for_suffix
        ):
            raise UploadValidationError(
                "upload_type_invalid",
                "upload type is not supported",
            )

        content = bytearray()
        while True:
            remaining = policy.max_bytes - len(content)
            chunk = await upload.read(min(self.chunk_bytes, remaining + 1))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > policy.max_bytes:
                raise UploadValidationError(
                    "upload_too_large",
                    "upload exceeds size limit",
                )

        if not content:
            raise UploadValidationError("upload_empty", "upload is empty")

        data = bytes(content)
        return ValidatedUpload(
            filename=filename,
            mime_type=mime_type,
            content=data,
            size_bytes=len(data),
            content_hash=hashlib.sha256(data).hexdigest(),
        )
