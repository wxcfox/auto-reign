from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import re
from typing import Literal, NotRequired, TypedDict
from uuid import uuid4

from app.core.limits import MAX_CHAT_BLOCK_ID_LENGTH
from app.services.json_safety import JsonSafetyError, validate_json_value
from app.services.runtime_types import ToolCall, ToolResult


TextBlockStatus = Literal["streaming", "done"]
ToolBlockStatus = Literal["generating_arguments", "pending", "done", "error"]
_TOOL_OUTPUT_UNSET = object()
_CHAT_BLOCK_ID_PATTERN = re.compile(
    rf"[A-Za-z0-9._:-]{{1,{MAX_CHAT_BLOCK_ID_LENGTH}}}",
    flags=re.ASCII,
)


class TextBlock(TypedDict):
    id: str
    type: Literal["text"]
    content: str
    status: TextBlockStatus
    timestamp: str


class ToolBlock(TypedDict):
    id: str
    type: Literal["tool"]
    tool_use_id: str
    tool_name: str
    tool_input: dict[str, object]
    tool_output: NotRequired[object]
    status: ToolBlockStatus
    timestamp: str


def is_valid_chat_block_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and _CHAT_BLOCK_ID_PATTERN.fullmatch(value) is not None
    )


def validate_chat_block_id(value: object) -> str:
    if not is_valid_chat_block_id(value):
        raise ValueError("chat_block_invalid_id")
    assert isinstance(value, str)
    return value


def copy_chat_block(block: object) -> TextBlock | ToolBlock:
    """Validate and defensively copy one canonical persisted chat block."""

    if not isinstance(block, dict):
        raise ValueError("chat_block_invalid_block")
    block_type = block.get("type")
    if block_type == "text":
        return _copy_text_block(block)
    if block_type == "tool":
        return _copy_tool_block(block)
    raise ValueError("chat_block_invalid_block")


def create_text_block(
    content: str = "",
    *,
    status: TextBlockStatus = "streaming",
    block_id: str | None = None,
    timestamp: datetime | None = None,
) -> TextBlock:
    if not isinstance(content, str):
        raise ValueError("chat_block_invalid_text")
    _validate_id(block_id)
    if status not in {"streaming", "done"}:
        raise ValueError("chat_block_invalid_text_status")
    block: TextBlock = {
        "id": block_id or str(uuid4()),
        "type": "text",
        "content": content,
        "status": status,
        "timestamp": _timestamp(timestamp),
    }
    return _copy_text_block(block)


def append_text_block(
    block: TextBlock,
    content: str,
    *,
    status: TextBlockStatus | None = None,
) -> TextBlock:
    current = _copy_text_block(block)
    if current["status"] == "done":
        raise ValueError("chat_block_terminal_status")
    if not isinstance(content, str):
        raise ValueError("chat_block_invalid_text")
    next_status = current["status"] if status is None else status
    if next_status not in {"streaming", "done"}:
        raise ValueError("chat_block_invalid_text_status")
    current["content"] += content
    current["status"] = next_status
    return _copy_text_block(current)


def create_tool_block(
    call: ToolCall,
    *,
    status: ToolBlockStatus = "pending",
    block_id: str | None = None,
    timestamp: datetime | None = None,
) -> ToolBlock:
    _validate_id(block_id)
    if (
        not isinstance(call.id, str)
        or not call.id.strip()
        or not isinstance(call.name, str)
        or not call.name.strip()
    ):
        raise ValueError("chat_block_invalid_tool")
    if status not in {"generating_arguments", "pending"}:
        raise ValueError("chat_block_invalid_tool_status")
    _ensure_json_safe(call.arguments)
    block: ToolBlock = {
        "id": block_id or str(uuid4()),
        "type": "tool",
        "tool_use_id": call.id,
        "tool_name": call.name,
        "tool_input": deepcopy(call.arguments),
        "status": status,
        "timestamp": _timestamp(timestamp),
    }
    return _copy_tool_block(block)


def update_tool_block(
    block: ToolBlock,
    *,
    tool_input: dict[str, object] | None = None,
    tool_output: object = _TOOL_OUTPUT_UNSET,
    status: ToolBlockStatus | None = None,
) -> ToolBlock:
    current = _copy_tool_block(block)
    old_status = current["status"]
    next_status = old_status if status is None else status
    if next_status not in {"generating_arguments", "pending", "done", "error"}:
        raise ValueError("chat_block_invalid_tool_status")
    if old_status in {"done", "error"}:
        raise ValueError("chat_block_terminal_status")
    allowed: dict[ToolBlockStatus, set[ToolBlockStatus]] = {
        "generating_arguments": {"generating_arguments", "pending", "error"},
        "pending": {"pending", "done", "error"},
        "done": {"done"},
        "error": {"error"},
    }
    if next_status not in allowed[old_status]:
        raise ValueError("chat_block_invalid_status_transition")
    if tool_input is not None:
        if old_status != "generating_arguments":
            raise ValueError("chat_block_tool_input_finalized")
        _ensure_json_safe(tool_input)
        current["tool_input"] = deepcopy(tool_input)
    if tool_output is not _TOOL_OUTPUT_UNSET:
        _ensure_json_safe(tool_output)
        current["tool_output"] = deepcopy(tool_output)
    current["status"] = next_status
    return _copy_tool_block(current)


def apply_tool_result(block: ToolBlock, result: ToolResult) -> ToolBlock:
    if result.call_id != block.get("tool_use_id"):
        raise ValueError("chat_block_tool_result_mismatch")
    return update_tool_block(
        block,
        tool_output=result.content,
        status="error" if result.is_error else "done",
    )


def _copy_text_block(block: object) -> TextBlock:
    expected = {"id", "type", "content", "status", "timestamp"}
    if isinstance(block, dict):
        _validate_existing_id(block.get("id"))
    if (
        not isinstance(block, dict)
        or set(block) != expected
        or block.get("type") != "text"
    ):
        raise ValueError("chat_block_invalid_text_block")
    copied = deepcopy(block)
    if not isinstance(copied.get("content"), str):
        raise ValueError("chat_block_invalid_text_block")
    if copied.get("status") not in {"streaming", "done"}:
        raise ValueError("chat_block_invalid_text_status")
    _validate_canonical_timestamp(copied.get("timestamp"))
    _ensure_json_safe(block)
    return copied  # type: ignore[return-value]


def _copy_tool_block(block: object) -> ToolBlock:
    required = {
        "id",
        "type",
        "tool_use_id",
        "tool_name",
        "tool_input",
        "status",
        "timestamp",
    }
    if isinstance(block, dict):
        _validate_existing_id(block.get("id"))
    allowed = (required, {*required, "tool_output"})
    if (
        not isinstance(block, dict)
        or not required.issubset(block)
        or set(block) not in allowed
        or block.get("type") != "tool"
    ):
        raise ValueError("chat_block_invalid_tool_block")
    if (
        not isinstance(block.get("tool_use_id"), str)
        or not block["tool_use_id"].strip()
    ):
        raise ValueError("chat_block_invalid_tool")
    if (
        not isinstance(block.get("tool_name"), str)
        or not block["tool_name"].strip()
    ):
        raise ValueError("chat_block_invalid_tool")
    if not isinstance(block.get("tool_input"), dict):
        raise ValueError("chat_block_invalid_tool_input")
    _ensure_json_safe(block["tool_input"])
    if "tool_output" in block:
        _ensure_json_safe(block["tool_output"])
    status = block.get("status")
    if status not in {
        "generating_arguments",
        "pending",
        "done",
        "error",
    }:
        raise ValueError("chat_block_invalid_tool_status")
    has_output = "tool_output" in block
    if status in {"generating_arguments", "pending"} and has_output:
        raise ValueError("chat_block_invalid_tool_state")
    if status in {"done", "error"} and not has_output:
        raise ValueError("chat_block_invalid_tool_state")
    _validate_canonical_timestamp(block.get("timestamp"))
    _ensure_json_safe(block)
    copied = deepcopy(block)
    return copied  # type: ignore[return-value]


def _validate_id(value: object) -> None:
    if value is not None:
        validate_chat_block_id(value)


def _validate_existing_id(value: object) -> None:
    validate_chat_block_id(value)


def _timestamp(value: datetime | None) -> str:
    current = datetime.now(UTC) if value is None else value
    if not isinstance(current, datetime):
        raise ValueError("chat_block_invalid_timestamp")
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("chat_block_invalid_timestamp")
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _validate_canonical_timestamp(value: object) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("chat_block_invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        raise ValueError("chat_block_invalid_timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("chat_block_invalid_timestamp")
    if _timestamp(parsed) != value:
        raise ValueError("chat_block_invalid_timestamp")


def _ensure_json_safe(value: object) -> None:
    try:
        validate_json_value(value)
    except (JsonSafetyError, RecursionError, OverflowError):
        raise ValueError("chat_block_not_json_safe") from None
