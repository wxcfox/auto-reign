from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Protocol, cast

from app.services.chat_blocks import is_valid_chat_block_id
from app.services.json_safety import JsonSafetyError, canonical_json
from app.services.text_offsets import TextOffsetError, utf16_code_units


class ChatStreamError(ValueError):
    """Stable error raised for invalid or unavailable ephemeral stream state."""


class ChatStreamOffsetMismatch(ChatStreamError):
    def __init__(self) -> None:
        super().__init__("chat_stream_offset_mismatch")


class ChatStreamNotActive(ChatStreamError):
    def __init__(self) -> None:
        super().__init__("chat_stream_not_active")


class ChatStreamMalformedState(ChatStreamError):
    def __init__(self) -> None:
        super().__init__("chat_stream_malformed_state")


class ChatStreamStaleGeneration(ChatStreamError):
    def __init__(self) -> None:
        super().__init__("chat_stream_stale_generation")


@dataclass(frozen=True)
class ActiveStreamSnapshot:
    task_id: int
    subtask_id: int
    generation_id: str
    offset: int
    cached_content: str
    blocks: tuple[dict[str, object], ...]
    started_at: str
    last_activity_at: str
    status_updated: dict[str, object] | None


class ChatStreamStore(Protocol):
    async def start(self, *, task_id: int, subtask_id: int) -> str: ...

    async def get_active(self, *, task_id: int) -> ActiveStreamSnapshot | None: ...

    async def validate_generation(
        self,
        *,
        task_id: int,
        subtask_id: int,
        generation_id: str,
    ) -> None:
        """Atomically require that this generation owns the Task active slot."""
        ...

    async def append_text(
        self,
        *,
        subtask_id: int,
        generation_id: str,
        block_id: str,
        offset: int,
        content: str,
    ) -> int:
        """Append only at the browser-compatible UTF-16 code-unit offset."""
        ...

    async def upsert_block(
        self,
        *,
        subtask_id: int,
        generation_id: str,
        block: dict[str, object],
    ) -> None:
        """Insert or fully replace a canonical block without changing its first-seen order."""
        ...

    async def set_cancelled(self, *, subtask_id: int, generation_id: str) -> None: ...

    async def is_cancelled(self, *, subtask_id: int, generation_id: str) -> bool: ...

    async def set_status_snapshot(
        self,
        *,
        subtask_id: int,
        generation_id: str,
        payload: dict[str, object],
    ) -> None: ...

    async def finalize(
        self,
        *,
        task_id: int,
        subtask_id: int,
        generation_id: str,
    ) -> None: ...

    async def aclose(self) -> None: ...


def _positive_id(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"chat_stream_invalid_{name}")


def _block_id(value: object) -> None:
    if not is_valid_chat_block_id(value):
        raise ValueError("chat_stream_invalid_block_id")


def _generation_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("chat_stream_invalid_generation_id")


def _offset(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("chat_stream_invalid_offset")


def _text(value: object) -> None:
    try:
        utf16_code_units(value)
    except TextOffsetError:
        raise ValueError("chat_stream_invalid_content") from None


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    current = clock()
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise ValueError("chat_stream_invalid_clock")
    return current.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("chat_stream_invalid_status_snapshot")
    try:
        serialized = canonical_json(value)
        decoded = json.loads(serialized)
    except (JsonSafetyError, json.JSONDecodeError, RecursionError, OverflowError):
        raise ValueError("chat_stream_invalid_status_snapshot") from None
    if not isinstance(decoded, dict):
        raise ValueError("chat_stream_invalid_status_snapshot")
    return cast(dict[str, object], decoded)


def _decode_text(value: object) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="strict")
        except UnicodeError:
            raise ChatStreamMalformedState() from None
    if not isinstance(value, str):
        raise ChatStreamMalformedState()
    return value


def _result_code(result: object) -> str:
    if not isinstance(result, list | tuple) or not result:
        raise ChatStreamMalformedState()
    return _decode_text(result[0])


def _parse_positive(value: object) -> int | None:
    try:
        parsed = int(cast(str, value))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _validate_timestamp(value: object) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ChatStreamMalformedState()
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        raise ChatStreamMalformedState() from None
    if _timestamp(parsed) != value:
        raise ChatStreamMalformedState()


def _check_result(result: object) -> None:
    raw_code = _result_code(result)
    if raw_code == "ok":
        return
    if raw_code == "offset":
        raise ChatStreamOffsetMismatch()
    if raw_code == "not_active":
        raise ChatStreamNotActive()
    if raw_code == "stale":
        raise ChatStreamStaleGeneration()
    raise ChatStreamMalformedState()
