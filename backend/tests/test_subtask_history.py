from app.services.subtask_history import SubtaskHistoryProjector
from app.services.json_safety import MAX_JSON_STRING_CHARS


def _tool_call(call_id: str) -> dict[str, object]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "lookup", "arguments": "{}"},
    }


def test_completed_history_preserves_chain_with_defensive_copies() -> None:
    stored = {
        "messages_chain": [
            {
                "role": "assistant",
                "content": "thinking",
                "tool_calls": [_tool_call("call-1")],
            },
            {
                "role": "tool",
                "content": "tool result",
                "tool_call_id": "call-1",
                "name": "lookup",
            },
            {"role": "assistant", "content": "final"},
        ]
    }

    projected = SubtaskHistoryProjector().project_assistant(status="COMPLETED", result=stored)

    assert projected == stored["messages_chain"]
    assert projected is not stored["messages_chain"]
    assert projected[0] is not stored["messages_chain"][0]
    projected[0]["content"] = "changed"
    assert stored["messages_chain"][0]["content"] == "thinking"


def test_completed_history_only_allows_valid_assistant_tool_linkage() -> None:
    projector = SubtaskHistoryProjector()
    valid = {
        "messages_chain": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call("call-1")],
            },
            {
                "role": "tool",
                "content": "result",
                "tool_call_id": "call-1",
                "name": "lookup",
            },
            {"role": "assistant", "content": "final"},
        ]
    }
    assert [
        item["role"] for item in projector.project_assistant(status="COMPLETED", result=valid)
    ] == [
        "assistant",
        "tool",
        "assistant",
    ]

    for malformed in (
        [{"role": "system", "content": "no"}],
        [{"role": "user", "content": "no"}],
        [
            {
                "role": "tool",
                "content": "orphan",
                "tool_call_id": "call-1",
                "name": "lookup",
            }
        ],
        [
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("call-1")]},
            {"role": "assistant", "content": "", "tool_calls": [_tool_call("call-1")]},
        ],
    ):
        assert projector.project_assistant(
            status="COMPLETED", result={"messages_chain": malformed, "value": "fallback"}
        ) == [{"role": "assistant", "content": "fallback"}]


def test_completed_history_accepts_null_and_structured_assistant_content() -> None:
    stored = {
        "messages_chain": [
            {"role": "assistant", "content": None},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "structured"},
                    {"type": "image", "image_url": {"url": "https://example.test/a"}},
                ],
            },
        ]
    }

    projected = SubtaskHistoryProjector().project_assistant(status="COMPLETED", result=stored)

    assert projected == stored["messages_chain"]
    assert projected[1]["content"] is not stored["messages_chain"][1]["content"]


def test_pending_running_and_cancelled_history_do_not_replay_tool_state() -> None:
    projector = SubtaskHistoryProjector()
    stored = {
        "value": "partial",
        "messages_chain": [{"role": "tool", "content": "secret", "tool_call_id": "call-1"}],
    }
    assert projector.project_assistant(status="PENDING", result=stored) == []
    assert projector.project_assistant(status="RUNNING", result=stored) == []
    assert projector.project_assistant(status="CANCELLED", result=stored) == [
        {"role": "assistant", "content": "partial"}
    ]


def test_failed_history_never_exposes_stored_chain() -> None:
    stored = {
        "value": "partial answer",
        "messages_chain": [
            {"role": "tool", "content": "SECRET_TOOL_OUTPUT"},
            {"role": "assistant", "content": "also hidden"},
        ],
    }

    projected = SubtaskHistoryProjector().project_assistant(status="FAILED", result=stored)

    assert projected == [{"role": "assistant", "content": "partial answer"}]
    assert "SECRET_TOOL_OUTPUT" not in str(projected)


def test_history_safely_handles_empty_or_malformed_result_and_user_prompt() -> None:
    projector = SubtaskHistoryProjector()

    assert projector.project_user(prompt="raw prompt") == [
        {"role": "user", "content": "raw prompt"}
    ]
    assert projector.project_assistant(status="COMPLETED", result=None) == []
    assert projector.project_assistant(
        status="COMPLETED", result={"messages_chain": "not-a-list", "value": "fallback"}
    ) == [{"role": "assistant", "content": "fallback"}]
    assert projector.project_assistant(
        status="CANCELLED", result={"messages_chain": ["bad"], "value": "fallback"}
    ) == [{"role": "assistant", "content": "fallback"}]


def test_completed_history_falls_back_when_chain_exceeds_json_bounds() -> None:
    result = {
        "messages_chain": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "x" * (MAX_JSON_STRING_CHARS + 1),
                    }
                ],
            }
        ],
        "value": "fallback",
    }

    assert SubtaskHistoryProjector().project_assistant(
        status="COMPLETED", result=result
    ) == [{"role": "assistant", "content": "fallback"}]
