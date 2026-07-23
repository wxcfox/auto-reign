from datetime import UTC, datetime

import pytest

from app.core.limits import MAX_CHAT_BLOCK_ID_LENGTH
from app.services.chat_blocks import (
    append_text_block,
    apply_tool_result,
    copy_chat_block,
    create_text_block,
    create_tool_block,
    is_valid_chat_block_id,
    update_tool_block,
)
from app.services.json_safety import MAX_JSON_NODE_COUNT, MAX_JSON_STRING_CHARS
from app.services.runtime_types import ToolCall, ToolResult


NOW = datetime(2026, 7, 22, 1, 2, 3, tzinfo=UTC)


def test_text_block_append_lifecycle_is_immutable() -> None:
    original = create_text_block("你", block_id="text-1", timestamp=NOW)
    appended = append_text_block(original, "好")
    done = append_text_block(appended, "。", status="done")

    assert original == {
        "id": "text-1",
        "type": "text",
        "content": "你",
        "status": "streaming",
        "timestamp": "2026-07-22T01:02:03Z",
    }
    assert appended["content"] == "你好"
    assert done["content"] == "你好。"
    assert done["status"] == "done"
    with pytest.raises(ValueError, match="chat_block_terminal_status"):
        append_text_block(done, "extra")


def test_tool_block_lifecycle_preserves_inputs_and_matches_result() -> None:
    arguments = {"path": "notes.md", "nested": {"page": 1}}
    call = ToolCall(id="call-1", name="read_file", arguments=arguments)
    started = create_tool_block(call, block_id="tool-1", timestamp=NOW)
    result = apply_tool_result(
        started,
        ToolResult(call_id="call-1", content='{"ok":true}'),
    )

    arguments["path"] = "changed"
    assert started["status"] == "pending"
    assert "tool_output" not in started
    assert started["tool_input"]["path"] == "notes.md"
    assert result["status"] == "done"
    assert result["tool_output"] == '{"ok":true}'


def test_generating_arguments_can_be_finalized_without_mutating_source() -> None:
    block = create_tool_block(
        ToolCall(id="call-1", name="lookup", arguments={}),
        status="generating_arguments",
    )
    pending = update_tool_block(block, tool_input={"q": "hello"}, status="pending")

    assert block["tool_input"] == {}
    assert block["status"] == "generating_arguments"
    assert pending["tool_input"] == {"q": "hello"}
    assert pending["status"] == "pending"


@pytest.mark.parametrize(
    "operation",
    [
        lambda: create_text_block(block_id=""),
        lambda: create_text_block(status="bad"),
        lambda: create_tool_block(ToolCall(id="", name="x", arguments={})),
        lambda: create_tool_block(ToolCall(id="x", name="", arguments={})),
        lambda: create_tool_block(
            ToolCall(id="x", name="tool", arguments={"bad": object()})
        ),
    ],
)
def test_rejects_invalid_ids_statuses_and_non_json_values(operation) -> None:
    with pytest.raises(ValueError):
        operation()


@pytest.mark.parametrize(
    "block_id",
    [
        " ",
        "\u00a0",
        "\u2003",
        "é",
        "中",
        "!",
        "slash/id",
        "x" * (MAX_CHAT_BLOCK_ID_LENGTH + 1),
    ],
)
def test_block_ids_use_one_bounded_ascii_grammar(block_id: str) -> None:
    assert is_valid_chat_block_id(block_id) is False
    with pytest.raises(ValueError, match="^chat_block_invalid_id$"):
        create_text_block(block_id=block_id)
    with pytest.raises(ValueError, match="^chat_block_invalid_id$"):
        create_tool_block(
            ToolCall(id="call-1", name="tool", arguments={}),
            block_id=block_id,
        )

    text = create_text_block(block_id="valid-text")
    text["id"] = block_id
    with pytest.raises(ValueError, match="^chat_block_invalid_id$"):
        append_text_block(text, "more")
    with pytest.raises(ValueError, match="^chat_block_invalid_id$"):
        copy_chat_block(text)

    tool = create_tool_block(ToolCall(id="call-1", name="tool", arguments={}))
    tool["id"] = block_id
    with pytest.raises(ValueError, match="^chat_block_invalid_id$"):
        update_tool_block(tool, status="done", tool_output=None)
    with pytest.raises(ValueError, match="^chat_block_invalid_id$"):
        copy_chat_block(tool)


@pytest.mark.parametrize(
    "block_id",
    ["Az09._:-", "x" * MAX_CHAT_BLOCK_ID_LENGTH],
)
def test_ascii_block_id_grammar_accepts_all_supported_characters(
    block_id: str,
) -> None:
    assert is_valid_chat_block_id(block_id) is True
    assert create_text_block(block_id=block_id)["id"] == block_id


def test_rejects_invalid_tool_transition_and_mismatched_result() -> None:
    call = ToolCall(id="call-1", name="lookup", arguments={})
    pending = create_tool_block(call)
    with pytest.raises(ValueError, match="chat_block_invalid_status_transition"):
        update_tool_block(pending, status="generating_arguments")
    with pytest.raises(ValueError, match="chat_block_tool_result_mismatch"):
        apply_tool_result(pending, ToolResult(call_id="other", content="x"))


def test_updates_require_existing_block_ids() -> None:
    text = create_text_block("hello")
    tool = create_tool_block(ToolCall(id="call-1", name="lookup", arguments={}))
    text.pop("id")
    tool.pop("id")

    with pytest.raises(ValueError, match="^chat_block_invalid_id$"):
        append_text_block(text, " world")
    with pytest.raises(ValueError, match="^chat_block_invalid_id$"):
        update_tool_block(tool, status="done")


@pytest.mark.parametrize(
    "unsafe_output",
    [
        object(),
        float("nan"),
        {1: "non-string key"},
        {"nested": [float("inf")]},
    ],
)
def test_update_rejects_unsafe_existing_tool_output(unsafe_output: object) -> None:
    block = create_tool_block(ToolCall(id="call-1", name="lookup", arguments={}))
    block["tool_output"] = unsafe_output

    with pytest.raises(ValueError, match="^chat_block_not_json_safe$"):
        update_tool_block(block, status="done")


@pytest.mark.parametrize(
    "timestamp",
    [
        "not-a-timestamp",
        "2026-07-22T01:02:03",
        "2026-07-22T01:02:03+00:00",
        "2026-07-22T09:02:03+08:00",
        False,
        0,
        [],
    ],
)
def test_updates_require_canonical_utc_timestamp(timestamp: object) -> None:
    text = create_text_block("hello")
    tool = create_tool_block(ToolCall(id="call-1", name="lookup", arguments={}))
    text["timestamp"] = timestamp  # type: ignore[typeddict-item]
    tool["timestamp"] = timestamp  # type: ignore[typeddict-item]

    with pytest.raises(ValueError, match="^chat_block_invalid_timestamp$"):
        append_text_block(text, " world")
    with pytest.raises(ValueError, match="^chat_block_invalid_timestamp$"):
        update_tool_block(tool, status="done")


@pytest.mark.parametrize("timestamp", [False, 0, "", []])
def test_create_rejects_explicit_falsy_timestamps(timestamp: object) -> None:
    call = ToolCall(id="call-1", name="lookup", arguments={})

    with pytest.raises(ValueError, match="^chat_block_invalid_timestamp$"):
        create_text_block(timestamp=timestamp)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="^chat_block_invalid_timestamp$"):
        create_tool_block(call, timestamp=timestamp)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["self_list", "self_dict", "mutual"])
def test_tool_json_cycles_raise_stable_validation_error(kind: str) -> None:
    if kind == "self_list":
        value: object = []
        value.append(value)
    elif kind == "self_dict":
        value = {}
        value["cycle"] = value
    else:
        mapping: dict[str, object] = {}
        sequence: list[object] = [mapping]
        mapping["cycle"] = sequence
        value = mapping

    block = create_tool_block(ToolCall(id="call-1", name="lookup", arguments={}))
    block["tool_output"] = value
    with pytest.raises(ValueError, match="^chat_block_not_json_safe$"):
        update_tool_block(block, status="done")


def test_tool_json_allows_shared_acyclic_references_defensively() -> None:
    shared = {"value": "same"}
    output = [shared, shared]
    block = create_tool_block(ToolCall(id="call-1", name="lookup", arguments={}))

    updated = update_tool_block(block, tool_output=output, status="done")

    shared["value"] = "changed"
    assert updated["tool_output"] == [
        {"value": "same"},
        {"value": "same"},
    ]


def test_tool_json_rejects_deep_oversized_and_excessive_nodes_stably() -> None:
    deep: dict[str, object] = {"value": "leaf"}
    for _ in range(1_200):
        deep = {"nested": deep}
    values = (
        deep,
        "x" * (MAX_JSON_STRING_CHARS + 1),
        [0] * (MAX_JSON_NODE_COUNT + 1),
    )
    block = create_tool_block(ToolCall(id="call-1", name="lookup", arguments={}))

    for value in values:
        with pytest.raises(ValueError, match="^chat_block_not_json_safe$"):
            update_tool_block(block, tool_output=value, status="done")

    with pytest.raises(ValueError, match="^chat_block_not_json_safe$"):
        create_text_block("x" * (MAX_JSON_STRING_CHARS + 1))


def test_blocks_reject_unknown_fields_even_when_values_are_not_json_safe() -> None:
    text = create_text_block("hello")
    tool = create_tool_block(ToolCall(id="call-1", name="lookup", arguments={}))
    text["unknown"] = object()
    tool["unknown"] = object()

    with pytest.raises(ValueError, match="^chat_block_invalid_text_block$"):
        append_text_block(text, " world")
    with pytest.raises(ValueError, match="^chat_block_invalid_tool_block$"):
        update_tool_block(tool, status="done", tool_output="result")


def test_explicit_empty_status_is_invalid_instead_of_preserving_status() -> None:
    text = create_text_block("hello")
    tool = create_tool_block(ToolCall(id="call-1", name="lookup", arguments={}))

    with pytest.raises(ValueError, match="^chat_block_invalid_text_status$"):
        append_text_block(text, " world", status="")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="^chat_block_invalid_tool_status$"):
        update_tool_block(tool, status="")  # type: ignore[arg-type]


def test_tool_output_must_match_tool_status_lifecycle() -> None:
    call = ToolCall(id="call-1", name="lookup", arguments={})
    pending = create_tool_block(call)
    premature = dict(pending)
    premature["tool_output"] = "too early"

    with pytest.raises(ValueError, match="^chat_block_invalid_tool_state$"):
        update_tool_block(premature, status="done")
    with pytest.raises(ValueError, match="^chat_block_invalid_tool_state$"):
        update_tool_block(pending, status="done")

    done = update_tool_block(pending, status="done", tool_output=None)
    assert "tool_output" in done
    assert done["tool_output"] is None
