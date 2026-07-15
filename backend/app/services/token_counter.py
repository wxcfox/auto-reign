from __future__ import annotations

from collections.abc import Sequence
import json

from app.services.runtime_types import ToolCall, ToolDefinition


_IMAGE_BLOCK_TYPES = frozenset({"image_url", "input_image", "image"})
_MESSAGE_FRAME_RESERVE = 16
_TOOL_SCHEMA_FRAME_RESERVE = 32


def _normalize_images(value: object) -> tuple[object, int]:
    if isinstance(value, dict):
        if value.get("type") in _IMAGE_BLOCK_TYPES:
            return {"type": value.get("type"), "image": "<reserved>"}, 1
        normalized: dict[str, object] = {}
        image_count = 0
        for key, item in value.items():
            normalized_item, nested_count = _normalize_images(item)
            normalized[str(key)] = normalized_item
            image_count += nested_count
        return normalized, image_count
    if isinstance(value, (list, tuple)):
        normalized_items: list[object] = []
        image_count = 0
        for item in value:
            normalized_item, nested_count = _normalize_images(item)
            normalized_items.append(normalized_item)
            image_count += nested_count
        return normalized_items, image_count
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value, 0
    raise TypeError(f"unsupported model input type: {type(value).__name__}")


def _utf8_json_bytes(value: object) -> int:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return len(serialized.encode("utf-8"))


class RuntimeTokenCounter:
    """Estimate one runtime context with a shared deterministic budget model."""

    def __init__(self, *, image_input_token_reserve: int) -> None:
        if image_input_token_reserve <= 0:
            raise ValueError("image_input_token_reserve must be positive")
        self.image_input_token_reserve = image_input_token_reserve

    def count_model_input(
        self,
        messages: Sequence[dict[str, object]],
        *,
        tools: tuple[ToolDefinition, ...],
    ) -> int:
        tool_payloads = [
            {
                "type": "function",
                "function": {
                    "name": item.name,
                    "description": item.description,
                    "parameters": item.input_schema,
                },
            }
            for item in tools
        ]
        normalized_messages, image_count = _normalize_images(list(messages))
        normalized_tools, nested_tool_images = _normalize_images(tool_payloads)
        if nested_tool_images:
            raise ValueError("tool schemas cannot contain image input blocks")
        return (
            _utf8_json_bytes(
                {
                    "messages": normalized_messages,
                    "tools": normalized_tools,
                }
            )
            + len(messages) * _MESSAGE_FRAME_RESERVE
            + len(tools) * _TOOL_SCHEMA_FRAME_RESERVE
            + image_count * self.image_input_token_reserve
        )

    def count_assistant_tool_call(self, call: ToolCall) -> int:
        return self.count_model_input(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(
                                    call.arguments,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                }
            ],
            tools=(),
        )

    def count_tool_result(self, *, call_id: str, content: str) -> int:
        return self.count_model_input(
            [
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": content,
                }
            ],
            tools=(),
        )
