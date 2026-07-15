from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import io
from threading import Lock
from typing import Any, Literal
from xml.etree import ElementTree
from zipfile import BadZipFile, LargeZipFile, ZipFile

from pypdf import PdfReader, filters as pdf_filters
from pypdf.errors import LimitReachedError, PdfReadError

from app.core.limits import (
    DEFAULT_ATTACHMENT_MAX_DECOMPRESSED_BYTES,
    DEFAULT_ATTACHMENT_MAX_PARSED_CHARS,
    DEFAULT_ATTACHMENT_MAX_PDF_PAGES,
)

DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class ExtractedContent:
    kind: Literal["text", "image"]
    mime_type: str
    text: str | None


class ExtractionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _BudgetExceeded(RuntimeError):
    pass


class _Budget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def consume(self, amount: int) -> None:
        self.used += amount
        if self.used > self.limit:
            raise _BudgetExceeded


class _TextAccumulator:
    def __init__(self, limit: int) -> None:
        self._budget = _Budget(limit)
        self._parts: list[str] = []

    def append(self, text: str) -> None:
        self._budget.consume(len(text))
        self._parts.append(text)

    def finish(self) -> str:
        return "".join(self._parts).strip()


_PDF_LIMIT_LOCK = Lock()
_PDF_LIMIT_NAMES = (
    "MAX_DECLARED_STREAM_LENGTH",
    "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
    "JBIG2_MAX_OUTPUT_LENGTH",
    "LZW_MAX_OUTPUT_LENGTH",
    "RUN_LENGTH_MAX_OUTPUT_LENGTH",
    "ZLIB_MAX_OUTPUT_LENGTH",
    "FLATE_MAX_BUFFER_SIZE",
)
_MISSING_PDF_HOOKS = tuple(
    name for name in (*_PDF_LIMIT_NAMES, "decode_stream_data") if not hasattr(pdf_filters, name)
)
if _MISSING_PDF_HOOKS:
    missing = ", ".join(_MISSING_PDF_HOOKS)
    raise RuntimeError(f"unsupported pypdf version; missing bounded decode hooks: {missing}")


@contextmanager
def _bounded_pdf_decode(max_bytes: int) -> Iterator[None]:
    """Bound pypdf's per-stream and cumulative decoded output under a process lock."""
    with _PDF_LIMIT_LOCK:
        previous_limits = {name: getattr(pdf_filters, name) for name in _PDF_LIMIT_NAMES}
        original_decode = pdf_filters.decode_stream_data
        budget = _Budget(max_bytes)

        def decode_with_budget(stream: Any) -> bytes:
            decoded = original_decode(stream)
            budget.consume(len(decoded))
            return decoded

        try:
            for name in _PDF_LIMIT_NAMES:
                setattr(pdf_filters, name, max_bytes)
            pdf_filters.decode_stream_data = decode_with_budget
            yield
        finally:
            pdf_filters.decode_stream_data = original_decode
            for name, value in previous_limits.items():
                setattr(pdf_filters, name, value)


class ExtractionService:
    IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})

    def __init__(
        self,
        *,
        max_parsed_chars: int = DEFAULT_ATTACHMENT_MAX_PARSED_CHARS,
        max_decompressed_bytes: int = DEFAULT_ATTACHMENT_MAX_DECOMPRESSED_BYTES,
        max_pdf_pages: int = DEFAULT_ATTACHMENT_MAX_PDF_PAGES,
    ) -> None:
        limits = {
            "max_parsed_chars": max_parsed_chars,
            "max_decompressed_bytes": max_decompressed_bytes,
            "max_pdf_pages": max_pdf_pages,
        }
        invalid = next((name for name, value in limits.items() if value <= 0), None)
        if invalid is not None:
            raise ValueError(f"{invalid} must be positive")
        self.max_parsed_chars = max_parsed_chars
        self.max_decompressed_bytes = max_decompressed_bytes
        self.max_pdf_pages = max_pdf_pages

    def extract_required(
        self,
        filename: str,
        mime_type: str,
        content: bytes,
    ) -> ExtractedContent:
        if not content:
            raise ExtractionError("extraction_empty", "attachment is empty")
        if mime_type in self.IMAGE_TYPES:
            return ExtractedContent(kind="image", mime_type=mime_type, text=None)

        try:
            text = self._extract_supported_text(filename, mime_type, content)
        except (_BudgetExceeded, LimitReachedError) as exc:
            raise ExtractionError(
                "extraction_too_large",
                "complete extraction exceeds configured limit",
            ) from exc
        except ExtractionError:
            raise
        except (
            UnicodeError,
            BadZipFile,
            LargeZipFile,
            EOFError,
            KeyError,
            OSError,
            RuntimeError,
            ValueError,
            ElementTree.ParseError,
            PdfReadError,
        ) as exc:
            raise ExtractionError(
                "extraction_invalid",
                "attachment cannot be parsed",
            ) from exc

        if not text:
            raise ExtractionError(
                "extraction_empty",
                "attachment did not contain readable text",
            )
        return ExtractedContent(kind="text", mime_type=mime_type, text=text)

    def _extract_supported_text(
        self,
        filename: str,
        mime_type: str,
        content: bytes,
    ) -> str:
        del filename
        accumulator = _TextAccumulator(self.max_parsed_chars)
        if mime_type in {"text/plain", "text/markdown"}:
            accumulator.append(content.decode("utf-8"))
            return accumulator.finish()

        if mime_type == "application/pdf":
            with _bounded_pdf_decode(self.max_decompressed_bytes):
                reader = PdfReader(io.BytesIO(content), strict=True)
                if len(reader.pages) > self.max_pdf_pages:
                    raise _BudgetExceeded
                for index, page in enumerate(reader.pages):
                    if index:
                        accumulator.append("\n")
                    accumulator.append(page.extract_text() or "")
            return accumulator.finish()

        if mime_type == DOCX_MIME_TYPE:
            with ZipFile(io.BytesIO(content)) as archive:
                infos = archive.infolist()
                if sum(info.file_size for info in infos) > self.max_decompressed_bytes:
                    raise _BudgetExceeded
                document = archive.getinfo("word/document.xml")
                if document.file_size > self.max_decompressed_bytes:
                    raise _BudgetExceeded
                with archive.open(document) as stream:
                    xml = stream.read(self.max_decompressed_bytes + 1)
                if len(xml) > self.max_decompressed_bytes:
                    raise _BudgetExceeded
            root = ElementTree.fromstring(xml)
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
            for node in root.iter(namespace):
                accumulator.append(node.text or "")
            return accumulator.finish()

        raise ExtractionError(
            "extraction_unsupported",
            "attachment type is not supported",
        )
