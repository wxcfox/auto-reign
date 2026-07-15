from __future__ import annotations

import io
from importlib.metadata import version
from threading import Event, Lock, Thread
import zipfile

import pytest
from pypdf import PdfWriter, filters as pdf_filters
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

from app.services.extraction_service import ExtractionError, ExtractionService


PDF_LIMIT_NAMES = (
    "MAX_DECLARED_STREAM_LENGTH",
    "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
    "JBIG2_MAX_OUTPUT_LENGTH",
    "LZW_MAX_OUTPUT_LENGTH",
    "RUN_LENGTH_MAX_OUTPUT_LENGTH",
    "ZLIB_MAX_OUTPUT_LENGTH",
    "FLATE_MAX_BUFFER_SIZE",
)


def _pdf_with_text(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    escaped_text = text.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
    stream.set_data(f"BT /F1 12 Tf 10 10 Td ({escaped_text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _pdf_with_two_streams(size: int) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/ProcSet"): ArrayObject()}
    )
    streams = []
    for _ in range(2):
        stream = DecodedStreamObject()
        stream.set_data(b" " * size)
        streams.append(writer._add_object(stream.flate_encode()))
    page[NameObject("/Contents")] = ArrayObject(streams)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _docx_with_nodes(*texts: str, compression: int = zipfile.ZIP_STORED) -> bytes:
    body = "".join(f"<w:t>{text}</w:t>" for text in texts)
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"{body}</w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def test_supported_pypdf_minor_and_limit_hooks_are_present() -> None:
    assert version("pypdf").startswith("6.13.")
    for name in PDF_LIMIT_NAMES:
        assert hasattr(pdf_filters, name), name
    assert callable(pdf_filters.decode_stream_data)


def test_parser_returns_complete_text_without_generating_a_summary() -> None:
    parsed = ExtractionService(
        max_parsed_chars=20,
        max_decompressed_bytes=1_024,
    ).extract_required(
        "note.txt",
        "text/plain",
        "学习原文".encode(),
    )

    assert parsed.kind == "text"
    assert parsed.mime_type == "text/plain"
    assert parsed.text == "学习原文"


@pytest.mark.parametrize("mime_type", ["image/png", "image/jpeg", "image/webp", "image/gif"])
def test_parser_keeps_supported_images_as_visual_input(mime_type: str) -> None:
    parsed = ExtractionService().extract_required("image", mime_type, b"image-data")

    assert parsed.kind == "image"
    assert parsed.mime_type == mime_type
    assert parsed.text is None


def test_parser_extracts_docx_text_nodes_in_document_order() -> None:
    parsed = ExtractionService().extract_required(
        "a.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _docx_with_nodes("Hello", " world"),
    )

    assert parsed.kind == "text"
    assert parsed.text == "Hello world"


def test_parser_extracts_pdf_text() -> None:
    parsed = ExtractionService().extract_required(
        "a.pdf",
        "application/pdf",
        _pdf_with_text("12345"),
    )

    assert parsed.kind == "text"
    assert parsed.text == "12345"


def test_txt_at_exact_character_limit_succeeds() -> None:
    parsed = ExtractionService(max_parsed_chars=5).extract_required(
        "a.txt",
        "text/plain",
        b"12345",
    )

    assert parsed.text == "12345"


def test_single_page_pdf_at_exact_character_limit_succeeds() -> None:
    parsed = ExtractionService(max_parsed_chars=5).extract_required(
        "a.pdf",
        "application/pdf",
        _pdf_with_text("12345"),
    )

    assert parsed.text == "12345"


def test_txt_overflow_fails_instead_of_truncating() -> None:
    with pytest.raises(ExtractionError) as captured:
        ExtractionService(max_parsed_chars=4).extract_required(
            "a.txt",
            "text/plain",
            b"12345",
        )

    assert captured.value.code == "extraction_too_large"


def test_docx_accumulated_text_overflow_fails() -> None:
    with pytest.raises(ExtractionError) as captured:
        ExtractionService(max_parsed_chars=5).extract_required(
            "a.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx_with_nodes("123", "456"),
        )

    assert captured.value.code == "extraction_too_large"


def test_docx_declared_decompressed_size_is_bounded() -> None:
    with pytest.raises(ExtractionError) as captured:
        ExtractionService(max_decompressed_bytes=1_024).extract_required(
            "a.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx_with_nodes("x" * 10_000, compression=zipfile.ZIP_DEFLATED),
        )

    assert captured.value.code == "extraction_too_large"


def test_pdf_text_overflow_fails_without_returning_a_prefix() -> None:
    with pytest.raises(ExtractionError) as captured:
        ExtractionService(max_parsed_chars=4).extract_required(
            "a.pdf",
            "application/pdf",
            _pdf_with_text("12345"),
        )

    assert captured.value.code == "extraction_too_large"


def test_pdf_decode_budget_is_cumulative_across_streams() -> None:
    with pytest.raises(ExtractionError) as captured:
        ExtractionService(max_decompressed_bytes=30).extract_required(
            "a.pdf",
            "application/pdf",
            _pdf_with_two_streams(20),
        )

    assert captured.value.code == "extraction_too_large"


def test_pdf_page_count_is_bounded_before_extraction() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(ExtractionError) as captured:
        ExtractionService(max_pdf_pages=1).extract_required(
            "a.pdf",
            "application/pdf",
            buffer.getvalue(),
        )

    assert captured.value.code == "extraction_too_large"


def test_pdf_limits_and_decoder_are_restored_after_failure() -> None:
    previous_limits = {name: getattr(pdf_filters, name) for name in PDF_LIMIT_NAMES}
    previous_decoder = pdf_filters.decode_stream_data

    with pytest.raises(ExtractionError):
        ExtractionService(max_parsed_chars=4).extract_required(
            "a.pdf",
            "application/pdf",
            _pdf_with_text("12345"),
        )

    assert pdf_filters.decode_stream_data is previous_decoder
    assert {name: getattr(pdf_filters, name) for name in PDF_LIMIT_NAMES} == previous_limits


def test_threaded_pdf_limits_do_not_leak_between_service_configurations(monkeypatch) -> None:
    original_decoder = pdf_filters.decode_stream_data
    first_decode_entered = Event()
    second_decode_entered = Event()
    release_first = Event()
    second_started = Event()
    call_lock = Lock()
    observed_limits: list[tuple[int, ...]] = []
    call_count = 0

    def blocking_decoder(stream):
        nonlocal call_count
        with call_lock:
            call_count += 1
            position = call_count
        if position == 1:
            first_decode_entered.set()
            if not release_first.wait(timeout=2):
                raise AssertionError("timed out waiting to release the first PDF decode")
        else:
            second_decode_entered.set()
        observed_limits.append(tuple(getattr(pdf_filters, name) for name in PDF_LIMIT_NAMES))
        return original_decoder(stream)

    monkeypatch.setattr(pdf_filters, "decode_stream_data", blocking_decoder)
    pdf = _pdf_with_text("threaded")
    results: list[str] = []
    failures: list[BaseException] = []

    def extract(limit: int, *, started: Event | None = None) -> None:
        if started is not None:
            started.set()
        try:
            parsed = ExtractionService(max_decompressed_bytes=limit).extract_required(
                "a.pdf", "application/pdf", pdf
            )
            results.append(parsed.text or "")
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    first = Thread(target=extract, args=(128,))
    first.start()
    assert first_decode_entered.wait(timeout=2)
    second = Thread(target=extract, args=(256,), kwargs={"started": second_started})
    second.start()
    assert second_started.wait(timeout=2)
    try:
        assert not second_decode_entered.wait(timeout=0.1)
    finally:
        release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    assert results == ["threaded", "threaded"]
    assert observed_limits == [(128,) * len(PDF_LIMIT_NAMES), (256,) * len(PDF_LIMIT_NAMES)]


@pytest.mark.parametrize(
    ("filename", "mime_type", "content", "code"),
    [
        ("archive.zip", "application/zip", b"zip", "extraction_unsupported"),
        ("empty.txt", "text/plain", b"", "extraction_empty"),
        ("blank.txt", "text/plain", b" \n\t", "extraction_empty"),
        ("bad.txt", "text/plain", b"\xff", "extraction_invalid"),
        ("bad.pdf", "application/pdf", b"not-a-pdf", "extraction_invalid"),
        (
            "bad.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"not-a-zip",
            "extraction_invalid",
        ),
        (
            "spoofed.docx",
            "application/x-wordprocessingml.document",
            _docx_with_nodes("text"),
            "extraction_unsupported",
        ),
    ],
)
def test_parser_returns_stable_errors_for_unsupported_empty_or_invalid_documents(
    filename: str,
    mime_type: str,
    content: bytes,
    code: str,
) -> None:
    with pytest.raises(ExtractionError) as captured:
        ExtractionService().extract_required(filename, mime_type, content)

    assert captured.value.code == code


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("max_parsed_chars", 0),
        ("max_decompressed_bytes", 0),
        ("max_pdf_pages", 0),
    ],
)
def test_parser_requires_positive_limits(keyword: str, value: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        ExtractionService(**{keyword: value})
