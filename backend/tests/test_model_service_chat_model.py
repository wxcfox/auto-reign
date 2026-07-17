from __future__ import annotations

from collections.abc import Iterator

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool

from app.services.model_service_chat_model import (
    ModelServiceChatModel,
    to_model_messages,
)
from app.services.runtime_types import ToolCall, ToolDefinition


class RecordingModelService:
    def __init__(self, *responses: tuple[str | ToolCall, ...]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def stream_turn(
        self,
        messages: list[dict[str, object]],
        *,
        provider: str,
        model: str,
        call_index: int,
        observer,
        tools: tuple[ToolDefinition, ...] | None = None,
    ) -> Iterator[str | ToolCall]:
        del observer
        self.calls.append(
            {
                "messages": messages,
                "provider": provider,
                "model": model,
                "call_index": call_index,
                "tools": tools,
            }
        )
        yield from self.responses.pop(0)


def _model(service: RecordingModelService) -> ModelServiceChatModel:
    return ModelServiceChatModel(
        model_service=service,
        provider_name="qwen",
        model_name="qwen-plus",
        runtime_observer=lambda _metrics: None,
    )


def test_chat_model_streams_text_and_preserves_provider_call_indexes() -> None:
    service = RecordingModelService(("one", "two"), ("three",))
    model = _model(service)

    first = list(model.stream([HumanMessage(content="hello")]))
    second = list(model.stream([HumanMessage(content="again")]))

    assert [chunk.content for chunk in first if chunk.content] == ["one", "two"]
    assert [chunk.content for chunk in second if chunk.content] == ["three"]
    assert [call["call_index"] for call in service.calls] == [1, 2]


def test_bind_tools_maps_tool_call_to_langchain_chunk() -> None:
    call = ToolCall(id="call-1", name="lookup", arguments={"key": "value"})
    service = RecordingModelService((call,))

    def lookup(key: str) -> str:
        return key

    tool = StructuredTool.from_function(
        func=lookup,
        name="lookup",
        description="Look up a value.",
    )
    bound = _model(service).bind_tools([tool])
    chunks = list(bound.stream([HumanMessage(content="find it")]))

    tool_chunks = [chunk for chunk in chunks if chunk.tool_calls]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].tool_calls == [
        {
            "name": "lookup",
            "args": {"key": "value"},
            "id": "call-1",
            "type": "tool_call",
        }
    ]
    assert service.calls[0]["tools"] == (
        ToolDefinition(
            name="lookup",
            description="Look up a value.",
            input_schema=tool.args_schema.model_json_schema(),
        ),
    )


def test_message_conversion_preserves_tool_linkage_for_model_service() -> None:
    messages = [
        HumanMessage(content="go"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "lookup",
                    "args": {"key": "value"},
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content='{"value":"found"}',
            tool_call_id="call-1",
            name="lookup",
        ),
    ]

    converted = to_model_messages(messages)

    assert converted[-2]["content"] is None
    assert converted[-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"value":"found"}',
    }
