from __future__ import annotations

import json

import pytest

from app.services.runtime_types import ToolCall, ToolDefinition
from app.services.token_counter import RuntimeTokenCounter


def _message_with_images(count: int) -> dict[str, object]:
    return {
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,payload-{index}"},
            }
            for index in range(count)
        ],
    }


def test_tool_result_counter_matches_model_input_counter() -> None:
    counter = RuntimeTokenCounter(image_input_token_reserve=4_096)
    content = '{"path":"notes.md","content":"完整内容"}'

    assert counter.count_tool_result(
        call_id="call-1",
        content=content,
    ) == counter.count_model_input(
        [{"role": "tool", "tool_call_id": "call-1", "content": content}],
        tools=(),
    )


def test_assistant_tool_call_counter_matches_model_input_counter() -> None:
    counter = RuntimeTokenCounter(image_input_token_reserve=4_096)
    call = ToolCall(
        id="call-一",
        name="read_file",
        arguments={"path": "学习/记录.md"},
    )
    arguments = json.dumps(
        call.arguments,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    assert counter.count_assistant_tool_call(call) == counter.count_model_input(
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
                            "arguments": arguments,
                        },
                    }
                ],
            }
        ],
        tools=(),
    )


def test_counter_uses_utf8_bytes_for_multibyte_text() -> None:
    counter = RuntimeTokenCounter(image_input_token_reserve=4_096)
    ascii_count = counter.count_model_input(
        [{"role": "user", "content": "aa"}],
        tools=(),
    )
    multibyte_count = counter.count_model_input(
        [{"role": "user", "content": "😀罕"}],
        tools=(),
    )

    assert multibyte_count - ascii_count >= (
        len("😀罕".encode()) - len(b"aa")
    )


def test_counter_reserves_configured_budget_for_every_image() -> None:
    counter = RuntimeTokenCounter(image_input_token_reserve=4_096)
    one = counter.count_model_input([_message_with_images(1)], tools=())
    three = counter.count_model_input([_message_with_images(3)], tools=())

    assert three - one >= 2 * 4_096


@pytest.mark.parametrize("image_type", ["image_url", "input_image", "image"])
def test_counter_does_not_double_count_image_payload_text(image_type: str) -> None:
    counter = RuntimeTokenCounter(image_input_token_reserve=512)
    small = {
        "role": "user",
        "content": [{"type": image_type, "payload": "x"}],
    }
    large = {
        "role": "user",
        "content": [{"type": image_type, "payload": "x" * 100_000}],
    }

    assert counter.count_model_input([small], tools=()) == counter.count_model_input(
        [large],
        tools=(),
    )


def test_counter_rejects_images_hidden_in_tool_schemas() -> None:
    counter = RuntimeTokenCounter(image_input_token_reserve=512)
    tool = ToolDefinition(
        name="unsafe",
        description="unsafe schema",
        input_schema={
            "type": "object",
            "properties": {
                "payload": {"type": "image_url", "image_url": "secret"}
            },
        },
    )

    with pytest.raises(ValueError, match="tool schemas cannot contain"):
        counter.count_model_input([], tools=(tool,))


def test_counter_rejects_unsupported_values_without_echoing_them() -> None:
    class Unsupported:
        def __repr__(self) -> str:
            return "do-not-echo"

    counter = RuntimeTokenCounter(image_input_token_reserve=512)

    with pytest.raises(TypeError) as captured:
        counter.count_model_input(
            [{"role": "user", "content": Unsupported()}],
            tools=(),
        )

    assert "do-not-echo" not in str(captured.value)


@pytest.mark.parametrize("reserve", [0, -1])
def test_counter_requires_a_positive_image_reserve(reserve: int) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        RuntimeTokenCounter(image_input_token_reserve=reserve)


def test_counter_is_deterministic_and_does_not_mutate_inputs() -> None:
    counter = RuntimeTokenCounter(image_input_token_reserve=512)
    messages = [_message_with_images(1)]
    original = json.loads(json.dumps(messages))
    tools = (
        ToolDefinition(
            name="read_file",
            description="Read one file.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
    )

    first = counter.count_model_input(messages, tools=tools)
    second = counter.count_model_input(messages, tools=tools)

    assert first == second
    assert messages == original


def test_counter_uses_the_documented_complete_json_and_frame_formula() -> None:
    reserve = 73
    counter = RuntimeTokenCounter(image_input_token_reserve=reserve)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "你好"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,secret"},
                },
            ],
        }
    ]
    tool = ToolDefinition(
        name="read_file",
        description="Read one file.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )
    normalized = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "你好"},
                    {"type": "image_url", "image": "<reserved>"},
                ],
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
        ],
    }
    serialized_bytes = len(
        json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )

    assert counter.count_model_input(messages, tools=(tool,)) == (
        serialized_bytes + 16 + 32 + reserve
    )
