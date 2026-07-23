from copy import deepcopy

import pytest

from app.services.json_safety import (
    MAX_JSON_NODE_COUNT,
    MAX_JSON_STRING_CHARS,
)
from app.services.message_chain import (
    serialize_assistant_event,
    serialize_tool_result,
    validate_messages_chain,
)
from app.services.runtime_types import AssistantMessageEvent, ToolCall, ToolResult


def _call(call_id: str = "call-1", name: str = "lookup") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments={"z": 1, "query": "值"})


def _round(call: ToolCall) -> list[dict[str, object]]:
    return [
        serialize_assistant_event(
            AssistantMessageEvent(
                content=None,
                tool_calls=(call,),
                reasoning_content="需要查询",
                provider="qwen",
                model="qwen-plus",
                compacted=True,
                summary_compacted=True,
                compaction_version=2,
            )
        ),
        serialize_tool_result(call, ToolResult(call.id, '{"value":"ok"}')),
    ]


def test_serializes_and_validates_complete_multi_round_chain() -> None:
    first = _call()
    second = ToolCall(id="call-2", name="fetch", arguments={"page": 2})
    chain = [
        *_round(first),
        *_round(second),
        serialize_assistant_event(
            AssistantMessageEvent(
                content="最终答案",
                provider="qwen",
                model="qwen-plus",
            )
        ),
    ]

    normalized = validate_messages_chain(chain)

    assert normalized == chain
    function = normalized[0]["tool_calls"][0]["function"]
    assert function["arguments"] == '{"query":"值","z":1}'
    assert normalized[0]["model_info"] == {
        "provider": "qwen",
        "model": "qwen-plus",
    }
    assert normalized[0]["compacted"] is True
    assert normalized[0]["summary_compacted"] is True
    assert normalized[0]["compaction_version"] == 2


def test_accepts_assistant_only_null_and_structured_content_defensively() -> None:
    chain = [
        {"role": "assistant", "content": None},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
        },
    ]
    original = deepcopy(chain)

    normalized = validate_messages_chain(chain)

    assert normalized == original
    assert normalized is not chain
    assert normalized[1]["content"] is not chain[1]["content"]
    normalized[1]["content"][0]["text"] = "changed"
    assert chain == original


@pytest.mark.parametrize(
    ("chain", "code"),
    [
        ([{"role": "system", "content": "privileged"}], "messages_chain_invalid_role"),
        ([{"role": "user", "content": "privileged"}], "messages_chain_invalid_role"),
        (
            [{"role": "tool", "tool_call_id": "call-1", "name": "x", "content": "x"}],
            "messages_chain_orphan_tool_result",
        ),
        (
            [
                *_round(_call()),
                {"role": "tool", "tool_call_id": "call-1", "name": "lookup", "content": "again"},
            ],
            "messages_chain_duplicate_tool_result",
        ),
        (
            [serialize_assistant_event(AssistantMessageEvent(None, (_call(),)))],
            "messages_chain_missing_tool_result",
        ),
            (
                [
                    *_round(_call()),
                    serialize_assistant_event(AssistantMessageEvent(None, (_call(),))),
                ],
            "messages_chain_duplicate_tool_call_id",
        ),
        (
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{ bad"},
                        }
                    ],
                }
            ],
            "messages_chain_invalid_tool_arguments",
        ),
        (
            [{"role": "assistant", "content": ["not-a-block"]}],
            "messages_chain_invalid_assistant_content",
        ),
        (
            [{"role": "tool", "tool_call_id": "x", "name": "x", "content": {}}],
            "messages_chain_invalid_tool_content",
        ),
    ],
)
def test_rejects_malformed_chains_with_stable_codes(
    chain: list[dict[str, object]], code: str
) -> None:
    with pytest.raises(ValueError, match=f"^{code}$"):
        validate_messages_chain(chain)


def test_serialize_tool_result_preserves_only_controlled_error_marker() -> None:
    call = _call()
    message = serialize_tool_result(
        call,
        ToolResult(
            call_id=call.id,
            content="failed",
            is_error=True,
            metadata={"terminal": True, "secret": "not persisted"},
        ),
    )

    assert message == {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": "failed",
        "is_error": True,
    }


def test_rejects_next_assistant_until_all_declared_tool_results_arrive() -> None:
    call = _call()
    chain = [
        serialize_assistant_event(AssistantMessageEvent(None, (call,))),
        {"role": "assistant", "content": "premature final"},
        serialize_tool_result(call, ToolResult(call.id, "late result")),
    ]

    with pytest.raises(ValueError, match="^messages_chain_missing_tool_result$"):
        validate_messages_chain(chain)


def test_accepts_multi_call_results_in_any_order_before_next_assistant() -> None:
    first = _call("call-1", "lookup")
    second = _call("call-2", "fetch")
    chain = [
        serialize_assistant_event(AssistantMessageEvent(None, (first, second))),
        serialize_tool_result(second, ToolResult(second.id, "second")),
        serialize_tool_result(first, ToolResult(first.id, "first")),
        {"role": "assistant", "content": "final"},
    ]

    assert validate_messages_chain(chain) == chain


@pytest.mark.parametrize("kind", ["self_list", "self_dict", "mutual"])
def test_structured_content_cycles_raise_stable_validation_error(kind: str) -> None:
    if kind == "self_list":
        content: list[object] = []
        content.append(content)
    elif kind == "self_dict":
        block: dict[str, object] = {"type": "text"}
        block["cycle"] = block
        content = [block]
    else:
        block = {"type": "text"}
        nested: list[object] = [block]
        block["cycle"] = nested
        content = [block]

    with pytest.raises(
        ValueError, match="^messages_chain_invalid_assistant_content$"
    ):
        validate_messages_chain([{"role": "assistant", "content": content}])


def test_structured_content_allows_shared_acyclic_references_defensively() -> None:
    shared = {"type": "text", "text": "shared"}
    content = [shared, shared]

    normalized = validate_messages_chain(
        [{"role": "assistant", "content": content}]
    )

    assert normalized[0]["content"] == content
    assert normalized[0]["content"] is not content
    shared["text"] = "changed"
    assert normalized[0]["content"][0]["text"] == "shared"


def test_structured_content_rejects_deep_oversized_and_excessive_nodes_stably() -> None:
    deep: dict[str, object] = {"value": "leaf"}
    for _ in range(1_200):
        deep = {"nested": deep}
    cases = (
        [{"type": "data", "value": deep}],
        [{"type": "text", "text": "x" * (MAX_JSON_STRING_CHARS + 1)}],
        [{"type": "data", "items": [0] * (MAX_JSON_NODE_COUNT + 1)}],
    )

    for content in cases:
        with pytest.raises(
            ValueError, match="^messages_chain_invalid_assistant_content$"
        ):
            validate_messages_chain([{"role": "assistant", "content": content}])


def test_rejects_excessive_chain_messages_with_stable_error() -> None:
    from app.services.message_chain import MAX_MESSAGE_CHAIN_MESSAGES

    chain = [
        {"role": "assistant", "content": "ok"}
        for _ in range(MAX_MESSAGE_CHAIN_MESSAGES + 1)
    ]

    with pytest.raises(ValueError, match="^messages_chain_too_large$"):
        validate_messages_chain(chain)


def test_rejects_excessive_tool_calls_with_stable_error() -> None:
    from app.services.message_chain import MAX_MESSAGE_CHAIN_TOOL_CALLS

    tool_calls = [
        {
            "id": f"call-{index}",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        }
        for index in range(MAX_MESSAGE_CHAIN_TOOL_CALLS + 1)
    ]

    with pytest.raises(ValueError, match="^messages_chain_too_large$"):
        validate_messages_chain(
            [{"role": "assistant", "content": None, "tool_calls": tool_calls}]
        )


def test_accepts_ordinary_long_tool_result_within_application_bounds() -> None:
    call = _call()
    content = "result" * 30_000
    chain = [
        serialize_assistant_event(AssistantMessageEvent(None, (call,))),
        serialize_tool_result(call, ToolResult(call.id, content)),
        {"role": "assistant", "content": "done"},
    ]

    assert validate_messages_chain(chain)[1]["content"] == content
