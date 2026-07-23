import pytest

from app.services.json_safety import MAX_JSON_CANONICAL_BYTES
from app.services.runtime_event_reducer import (
    ResultEnvelopeError,
    RuntimeEventReducer,
    RuntimeEventReductionError,
    validate_result_envelope,
)
from app.services.runtime_types import (
    AssistantMessageEvent,
    TextDeltaEvent,
    ToolCall,
    ToolResult,
    ToolResultEvent,
    ToolStartEvent,
)


def _reducer() -> RuntimeEventReducer:
    return RuntimeEventReducer(provider="qwen", model="qwen3.7-plus")


def _call(arguments: dict[str, object] | None = None) -> ToolCall:
    return ToolCall(id="call-1", name="lookup", arguments=arguments or {"q": "x"})


def _declare(reducer: RuntimeEventReducer, call: ToolCall) -> None:
    reducer.accept(AssistantMessageEvent(content="", tool_calls=(call,)))


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        (
            lambda reducer, call: reducer.accept(ToolStartEvent(call=call)),
            "runtime_event_orphan_tool_start",
        ),
        (
            lambda reducer, call: reducer.accept(
                ToolResultEvent(
                    call=call,
                    result=ToolResult(call_id=call.id, content="result"),
                )
            ),
            "runtime_event_orphan_tool_result",
        ),
    ],
)
def test_reducer_rejects_orphan_tool_events(operation, code: str) -> None:
    with pytest.raises(RuntimeEventReductionError) as captured:
        operation(_reducer(), _call())
    assert captured.value.code == code


def test_reducer_requires_exact_start_identity_and_rejects_duplicates() -> None:
    reducer = _reducer()
    declared = _call()
    _declare(reducer, declared)

    with pytest.raises(RuntimeEventReductionError) as mismatch:
        reducer.accept(ToolStartEvent(call=_call({"q": "different"})))
    assert mismatch.value.code == "runtime_event_tool_start_mismatch"

    reducer.accept(ToolStartEvent(call=declared))
    with pytest.raises(RuntimeEventReductionError) as duplicate:
        reducer.accept(ToolStartEvent(call=declared))
    assert duplicate.value.code == "runtime_event_duplicate_tool_start"


def test_reducer_snapshots_declared_tool_arguments() -> None:
    reducer = _reducer()
    arguments: dict[str, object] = {"nested": {"value": "original"}}
    declared = _call(arguments)
    _declare(reducer, declared)
    nested = arguments["nested"]
    assert isinstance(nested, dict)
    nested["value"] = "mutated"

    with pytest.raises(RuntimeEventReductionError) as captured:
        reducer.accept(ToolStartEvent(call=declared))

    assert captured.value.code == "runtime_event_tool_start_mismatch"


def test_reducer_requires_matching_single_result_and_no_unresolved_next_assistant() -> None:
    reducer = _reducer()
    call = _call()
    _declare(reducer, call)
    reducer.accept(ToolStartEvent(call=call))

    with pytest.raises(RuntimeEventReductionError) as unresolved:
        reducer.accept(AssistantMessageEvent(content="next"))
    assert unresolved.value.code == "runtime_event_unresolved_tool_call"

    result = ToolResult(call_id=call.id, content="result")
    reducer.accept(ToolResultEvent(call=call, result=result))
    with pytest.raises(RuntimeEventReductionError) as duplicate:
        reducer.accept(ToolResultEvent(call=call, result=result))
    assert duplicate.value.code == "runtime_event_duplicate_tool_result"


def test_reducer_rejects_delta_and_visible_assistant_mismatch() -> None:
    reducer = _reducer()
    reducer.accept(TextDeltaEvent(content="streamed"))

    with pytest.raises(RuntimeEventReductionError) as captured:
        reducer.accept(AssistantMessageEvent(content="different"))

    assert captured.value.code == "runtime_event_text_mismatch"


def test_chunk_offsets_use_browser_utf16_units_for_accumulated_text() -> None:
    reducer = _reducer()

    emoji_emissions = reducer.accept(TextDeltaEvent(content="😀"))
    following_emissions = reducer.accept(TextDeltaEvent(content="e\u0301中"))
    final_emissions = reducer.accept(TextDeltaEvent(content="x"))

    assert emoji_emissions[-1].type == "chunk"
    assert emoji_emissions[-1].data == {
        "block_id": emoji_emissions[-1].data["block_id"],
        "block_offset": 0,
        "offset": 0,
        "content": "😀",
    }
    assert following_emissions[-1].data == {
        "block_id": emoji_emissions[-1].data["block_id"],
        "block_offset": 2,
        "offset": 2,
        "content": "e\u0301中",
    }
    assert final_emissions[-1].data == {
        "block_id": emoji_emissions[-1].data["block_id"],
        "block_offset": 5,
        "offset": 5,
        "content": "x",
    }


def test_global_utf16_offset_continues_across_text_blocks() -> None:
    reducer = _reducer()
    call = _call()
    reducer.accept(TextDeltaEvent(content="😀"))
    reducer.accept(AssistantMessageEvent(content="😀", tool_calls=(call,)))
    reducer.accept(ToolStartEvent(call=call))
    reducer.accept(
        ToolResultEvent(
            call=call,
            result=ToolResult(call_id=call.id, content="result"),
        )
    )

    emissions = reducer.accept(TextDeltaEvent(content="中"))

    assert emissions[-1].data["block_offset"] == 0
    assert emissions[-1].data["offset"] == 2


def test_reducer_rejects_unpaired_surrogate_delta() -> None:
    reducer = _reducer()

    with pytest.raises(RuntimeEventReductionError) as captured:
        reducer.accept(TextDeltaEvent(content="\ud800"))

    assert captured.value.code == "runtime_event_invalid"


@pytest.mark.parametrize(
    "case",
    ["no_messages", "empty", "structured_empty", "tool_call", "tool_result"],
)
def test_success_requires_nonempty_terminal_assistant_answer(case: str) -> None:
    reducer = _reducer()
    if case == "no_messages":
        pass
    elif case == "empty":
        reducer.accept(AssistantMessageEvent(content=""))
    elif case == "structured_empty":
        reducer.accept(
            AssistantMessageEvent(content=[{"type": "image", "url": "private"}])
        )
    else:
        call = _call()
        _declare(reducer, call)
        if case == "tool_result":
            reducer.accept(ToolStartEvent(call=call))
            reducer.accept(
                ToolResultEvent(
                    call=call,
                    result=ToolResult(call_id=call.id, content="result"),
                )
            )

    with pytest.raises(RuntimeEventReductionError) as captured:
        reducer.finish_success()

    assert captured.value.code == "runtime_event_invalid_terminal"


def test_success_value_is_exactly_the_visible_final_assistant_answer() -> None:
    reducer = _reducer()
    call = _call()
    reducer.accept(TextDeltaEvent(content="preface"))
    reducer.accept(
        AssistantMessageEvent(content="preface", tool_calls=(call,))
    )
    reducer.accept(ToolStartEvent(call=call))
    reducer.accept(
        ToolResultEvent(
            call=call,
            result=ToolResult(call_id=call.id, content="result"),
        )
    )
    reducer.accept(TextDeltaEvent(content="final answer"))
    reducer.accept(
        AssistantMessageEvent(
            content=[{"type": "output_text", "text": "final answer"}]
        )
    )

    result = reducer.finish_success()

    assert result["value"] == "final answer"
    assert result["messages_chain"][-1]["content"] == [
        {"type": "output_text", "text": "final answer"}
    ]


def test_partial_result_terminalizes_pending_blocks_and_is_json_bounded() -> None:
    reducer = _reducer()
    call = _call()
    _declare(reducer, call)
    reducer.accept(ToolStartEvent(call=call))

    result = reducer.partial_result()

    assert result["blocks"][0]["status"] == "error"
    validate_result_envelope(result, success=False)


def test_result_envelope_rejects_total_canonical_size_overflow() -> None:
    result = {
        "value": "x" * (MAX_JSON_CANONICAL_BYTES + 1),
        "messages_chain": [],
        "blocks": [],
        "context_compactions": [],
        "sources": [],
        "termination_reason": None,
    }

    with pytest.raises(ResultEnvelopeError) as captured:
        validate_result_envelope(result, success=True)

    assert captured.value.code == "runtime_result_too_large"


def test_reducer_bounds_sources_and_sanitizes_termination_reason() -> None:
    reducer = _reducer()
    call = _call()
    _declare(reducer, call)
    reducer.accept(ToolStartEvent(call=call))
    reducer.accept(
        ToolResultEvent(
            call=call,
            result=ToolResult(
                call_id=call.id,
                content="result",
                metadata={
                    "sources": [
                        *({"document_id": f"doc-{index}"} for index in range(25)),
                        {"unsafe": b"bytes"},
                    ],
                    "termination_reason": "bad\nreason",
                },
            ),
        )
    )
    reducer.accept(TextDeltaEvent(content="final"))
    reducer.accept(AssistantMessageEvent(content="final"))

    result = reducer.finish()

    assert len(result["sources"]) == 20
    assert result["sources"][-1] == {"document_id": "doc-19"}
    assert result["termination_reason"] is None
