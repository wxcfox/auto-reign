from __future__ import annotations


class TextOffsetError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def utf16_code_units(text: object) -> int:
    """Return JavaScript String.length for strict Unicode text."""

    if not isinstance(text, str):
        raise TextOffsetError("text_offset_invalid_text")
    try:
        encoded = text.encode("utf-16-le", errors="strict")
    except UnicodeError:
        raise TextOffsetError("text_offset_invalid_text") from None
    return len(encoded) // 2


def advance_utf16_offset(offset: object, content: object) -> int:
    """Advance a non-negative JS UTF-16 offset by one strict text chunk."""

    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise TextOffsetError("text_offset_invalid_offset")
    return offset + utf16_code_units(content)
