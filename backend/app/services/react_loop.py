from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
import json
from typing import Any
import warnings

from langchain_core.messages import (
    AIMessageChunk,
    ToolMessage,
    convert_to_messages,
)
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolNode
from langgraph.warnings import LangGraphDeprecatedSinceV10

from app.core.limits import (
    DEFAULT_RUNTIME_MAX_TOOL_ROUNDS,
    MAX_RUNTIME_MAX_TOOL_ROUNDS,
)
from app.services.langchain_tool_adapter import (
    CapabilityBaseTool,
    build_langchain_tools,
)
from app.services.model_service import ModelService
from app.services.model_service_chat_model import (
    ModelServiceChatModel,
    to_model_messages,
)
from app.services.runtime_types import (
    CapabilityContext,
    RuntimeObserver,
    RuntimeTerminalError,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from app.services.token_counter import RuntimeTokenCounter
from app.services.tool_registry import ToolRegistrySnapshot


class ReactLoop:
    """Run the model -> tool -> ToolMessage cycle for one prepared turn."""

    def __init__(
        self,
        *,
        model_service: ModelService,
        token_counter: RuntimeTokenCounter,
    ) -> None:
        self.model_service = model_service
        self.token_counter = token_counter
        self.max_tool_rounds = DEFAULT_RUNTIME_MAX_TOOL_ROUNDS

    def configure_max_tool_rounds(self, max_tool_rounds: int) -> None:
        if (
            type(max_tool_rounds) is not int
            or not 1 <= max_tool_rounds <= MAX_RUNTIME_MAX_TOOL_ROUNDS
        ):
            raise ValueError(
                f"max_tool_rounds must be between 1 and {MAX_RUNTIME_MAX_TOOL_ROUNDS}"
            )
        self.max_tool_rounds = max_tool_rounds

    def stream(
        self,
        messages: list[dict[str, object]],
        *,
        provider: str,
        model: str,
        context: CapabilityContext,
        registry: ToolRegistrySnapshot,
        observer: RuntimeObserver,
    ) -> Iterator[str | ToolResult]:
        chat_model = ModelServiceChatModel(
            model_service=self.model_service,
            provider_name=provider,
            model_name=model,
            runtime_observer=observer,
        )
        tools = build_langchain_tools(registry, context)
        tool_node = ToolNode(
            list(tools),
            wrap_tool_call=self._tool_wrapper(
                context=context,
                registry=registry,
            ),
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LangGraphDeprecatedSinceV10)
            graph = create_react_agent(
                model=chat_model,
                tools=tool_node,
                pre_model_hook=self._context_guard(
                    context=context,
                    registry=registry,
                ),
                version="v2",
            )
        try:
            for message, _metadata in graph.stream(
                {"messages": convert_to_messages(messages)},
                config={"recursion_limit": self.max_tool_rounds * 4 + 4},
                stream_mode="messages",
            ):
                if isinstance(message, AIMessageChunk):
                    if isinstance(message.content, str) and message.content:
                        yield message.content
                    continue
                if not isinstance(message, ToolMessage):
                    continue
                result = message.artifact
                if not isinstance(result, ToolResult):
                    raise TypeError("tool message did not preserve its audit artifact")
                yield result
                if result.metadata.get("terminal") is True:
                    raise _context_too_large_terminal()
        except (GraphRecursionError, ToolCallLimitExceeded) as error:
            raise RuntimeError("tool_call_limit_exceeded") from error
        except RuntimeError as error:
            message = str(error).split("\nDuring task with name", 1)[0]
            if message != str(error):
                error.args = (message, *error.args[1:])
            notes = getattr(error, "__notes__", None)
            if isinstance(notes, list):
                notes.clear()
            raise

    def _context_guard(
        self,
        *,
        context: CapabilityContext,
        registry: ToolRegistrySnapshot,
    ) -> Callable[[dict[str, Any]], dict[str, object]]:
        def guard(state: dict[str, Any]) -> dict[str, object]:
            messages = state.get("messages", ())
            completed_tool_rounds = sum(
                isinstance(message, ToolMessage) for message in messages
            )
            if completed_tool_rounds >= self.max_tool_rounds:
                raise ToolCallLimitExceeded()
            used = self.token_counter.count_model_input(
                to_model_messages(messages),
                tools=registry.definitions,
            )
            if used > context.token_budget:
                raise _context_too_large_terminal()
            return {"llm_input_messages": messages}

        return guard

    def _tool_wrapper(
        self,
        *,
        context: CapabilityContext,
        registry: ToolRegistrySnapshot,
    ) -> Callable[[ToolCallRequest, Callable[[ToolCallRequest], Any]], Any]:
        def execute_with_budget(
            request: ToolCallRequest,
            execute: Callable[[ToolCallRequest], Any],
        ) -> Any:
            call = _runtime_tool_call(request.tool_call)
            state_messages = request.state.get("messages", ())
            remaining = self._remaining_tokens(
                context=context,
                messages=to_model_messages(state_messages[:-1]),
                definitions=registry.definitions,
                call=call,
            )
            if remaining <= 0:
                return _tool_message(call, terminal_budget_audit(call))

            spec = registry.get(call.name)
            if spec is None:
                return _tool_message(call, registry.execute(call, context))
            budget_tool = CapabilityBaseTool.from_spec(
                spec=spec,
                context=replace(context, token_budget=remaining),
            )
            output = execute(
                ToolCallRequest(
                    tool_call=request.tool_call,
                    tool=budget_tool,
                    state=request.state,
                    runtime=request.runtime,
                )
            )
            if not isinstance(output, ToolMessage):
                return output
            candidate = output.artifact
            if not isinstance(candidate, ToolResult):
                raise TypeError("tool message did not preserve its ToolResult artifact")
            result = self._fit_result(
                call=call,
                candidate=candidate,
                remaining=remaining,
            )
            if result is None:
                return _tool_message(call, terminal_budget_audit(call))
            if result is candidate:
                return output
            return _tool_message(call, result)

        return execute_with_budget

    def _remaining_tokens(
        self,
        *,
        context: CapabilityContext,
        messages: list[dict[str, object]],
        definitions: tuple[ToolDefinition, ...],
        call: ToolCall,
    ) -> int:
        used = self.token_counter.count_model_input(
            messages,
            tools=definitions,
        ) + self.token_counter.count_assistant_tool_call(call)
        return context.token_budget - used

    def _fit_result(
        self,
        *,
        call: ToolCall,
        candidate: ToolResult,
        remaining: int,
    ) -> ToolResult | None:
        result = candidate
        if self._result_tokens(candidate) > remaining:
            result = context_too_large_result(call)
        return result if self._result_tokens(result) <= remaining else None

    def _result_tokens(self, result: ToolResult) -> int:
        return self.token_counter.count_tool_result(
            call_id=result.call_id,
            content=result.content,
        )


def context_too_large_result(call: ToolCall) -> ToolResult:
    return ToolResult(
        call_id=call.id,
        content=json.dumps(
            {
                "code": "context_too_large",
                "message": "The tool result exceeds the remaining context budget.",
            },
            separators=(",", ":"),
        ),
        is_error=True,
        metadata={"tool": call.name, "code": "context_too_large"},
    )


def terminal_budget_audit(call: ToolCall) -> ToolResult:
    return ToolResult(
        call_id=call.id,
        content="{}",
        is_error=True,
        metadata={
            "tool": call.name,
            "code": "context_too_large",
            "terminal": True,
        },
    )


def _context_too_large_terminal() -> RuntimeTerminalError:
    return RuntimeTerminalError(
        code="context_too_large",
        message="The conversation context is too large.",
        status_code=413,
    )


def _runtime_tool_call(value: object) -> ToolCall:
    if not isinstance(value, dict):
        raise TypeError("invalid LangGraph tool call")
    call_id = value.get("id")
    name = value.get("name")
    arguments = value.get("args")
    if (
        not isinstance(call_id, str)
        or not call_id
        or not isinstance(name, str)
        or not name
        or not isinstance(arguments, dict)
    ):
        raise TypeError("invalid LangGraph tool call")
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _tool_message(call: ToolCall, result: ToolResult) -> ToolMessage:
    return ToolMessage(
        content=result.content,
        tool_call_id=call.id,
        name=call.name,
        status="error" if result.is_error else "success",
        artifact=result,
    )


class ToolCallLimitExceeded(RuntimeError):
    pass
