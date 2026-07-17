from __future__ import annotations

from collections.abc import Iterator, Sequence
import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.chat_models import generate_from_stream
from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    convert_to_openai_messages,
)
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field, PrivateAttr

from app.services.runtime_types import (
    RuntimeObserver,
    ToolCall,
    ToolDefinition,
)


class ModelServiceChatModel(BaseChatModel):
    """Adapt the existing provider service to LangChain's chat model protocol."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_service: Any = Field(exclude=True)
    provider_name: str
    model_name: str
    runtime_observer: RuntimeObserver = Field(exclude=True)
    tool_definitions: tuple[ToolDefinition, ...] = ()
    _next_call_index: int = PrivateAttr(default=1)

    @property
    def _llm_type(self) -> str:
        return "auto_reign_model_service"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model_name,
        }

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        del tool_choice, kwargs
        definitions = tuple(_tool_definition(tool) for tool in tools)
        return type(self)(
            model_service=self.model_service,
            provider_name=self.provider_name,
            model_name=self.model_name,
            runtime_observer=self.runtime_observer,
            tool_definitions=definitions,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return generate_from_stream(
            self._stream(messages, stop=stop, **kwargs)
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        del stop, kwargs
        call_index = self._next_call_index
        self._next_call_index += 1
        for event in self.model_service.stream_turn(
            to_model_messages(messages),
            provider=self.provider_name,
            model=self.model_name,
            call_index=call_index,
            observer=self.runtime_observer,
            tools=self.tool_definitions or None,
        ):
            if isinstance(event, str):
                yield ChatGenerationChunk(
                    message=AIMessageChunk(content=event)
                )
                continue
            if isinstance(event, ToolCall):
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": event.name,
                                "args": json.dumps(
                                    event.arguments,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                                "id": event.id,
                                "index": 0,
                                "type": "tool_call_chunk",
                            }
                        ],
                    )
                )
                continue
            raise TypeError("unsupported model service event")


def to_model_messages(messages: Sequence[BaseMessage]) -> list[dict[str, object]]:
    converted = convert_to_openai_messages(list(messages))
    normalized: list[dict[str, object]] = []
    for message in converted:
        item = dict(message)
        if item.get("role") == "assistant" and item.get("tool_calls"):
            item["content"] = None
        if item.get("role") == "tool":
            item.pop("name", None)
        normalized.append(item)
    return normalized


def _tool_definition(tool: dict[str, Any] | type | BaseTool) -> ToolDefinition:
    if not isinstance(tool, BaseTool):
        raise TypeError("runtime tools must be LangChain BaseTool instances")
    schema = tool.args_schema
    if isinstance(schema, dict):
        input_schema = dict(schema)
    elif isinstance(schema, type) and hasattr(schema, "model_json_schema"):
        input_schema = schema.model_json_schema()
    else:
        raise TypeError("runtime tool schema is unavailable")
    return ToolDefinition(
        name=tool.name,
        description=tool.description,
        input_schema=input_schema,
    )
