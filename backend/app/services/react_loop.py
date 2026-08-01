from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
import json
from typing import Any
import warnings

from langchain_core.messages import (
    AIMessage,
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
    AssistantMessageEvent,
    CapabilityContext,
    RuntimeEvent,
    RuntimeObserver,
    RuntimeTerminalError,
    TextDeltaEvent,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolResultEvent,
    ToolStartEvent,
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
    ) -> Iterator[RuntimeEvent]:
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
        pending: AIMessageChunk | None = None
        pending_reasoning: list[str] = []
        active_calls: dict[str, ToolCall] = {}
        starts_emitted = False
        try:
            for message, _metadata in graph.stream(
                {"messages": convert_to_messages(messages)},
                config={"recursion_limit": self.max_tool_rounds * 4 + 4},
                stream_mode="messages",
            ):
                if isinstance(message, AIMessageChunk):
                    if pending is not None and _different_message(pending, message):
                        event = _assistant_event(
                            pending,
                            reasoning_content="".join(pending_reasoning) or None,
                            provider=provider,
                            model=model,
                        )
                        yield event
                        active_calls = {call.id: call for call in event.tool_calls}
                        for call in event.tool_calls:
                            yield ToolStartEvent(call=call)
                        starts_emitted = bool(event.tool_calls)
                        pending = None
                        pending_reasoning = []
                    pending = message if pending is None else pending + message
                    reasoning = _chunk_reasoning(message)
                    if reasoning:
                        pending_reasoning.append(reasoning)
                    for text in _chunk_text(message):
                        yield TextDeltaEvent(content=text)
                    continue
                if not isinstance(message, ToolMessage):
                    continue
                if pending is not None:
                    event = _assistant_event(
                        pending,
                        reasoning_content="".join(pending_reasoning) or None,
                        provider=provider,
                        model=model,
                    )
                    yield event
                    active_calls = {call.id: call for call in event.tool_calls}
                    for call in event.tool_calls:
                        yield ToolStartEvent(call=call)
                    starts_emitted = bool(event.tool_calls)
                    pending = None
                    pending_reasoning = []
                result = message.artifact
                if not isinstance(result, ToolResult):
                    raise _tool_protocol_violation(
                        "A tool result lost its audit artifact."
                    )
                if message.tool_call_id != result.call_id:
                    raise _tool_protocol_violation(
                        "A tool result did not match its audit artifact."
                    )
                call = active_calls.get(result.call_id)
                if call is None or not starts_emitted:
                    raise _tool_protocol_violation(
                        "A tool result did not match an assistant tool call."
                    )
                yield ToolResultEvent(call=call, result=result)
                if result.metadata.get("terminal") is True:
                    raise _context_too_large_terminal()
            if pending is not None:
                yield _assistant_event(
                    pending,
                    reasoning_content="".join(pending_reasoning) or None,
                    provider=provider,
                    model=model,
                )
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
            # One assistant turn is one round even when it carries several
            # parallel calls. Counting ToolMessages instead would let a single
            # parallel turn exhaust the limit and block the final answer.
            completed_tool_rounds = sum(
                isinstance(message, AIMessage) and bool(message.tool_calls)
                for message in messages
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
            # Parallel calls in one turn all receive the same pre-tool state, so
            # each must be given a share of the remaining budget instead of the
            # whole of it. Otherwise every sibling fits on its own and only the
            # merged state is found to be over budget, which fails the turn.
            siblings = _sibling_tool_calls(state_messages, call)
            remaining = self._remaining_tokens(
                context=context,
                messages=to_model_messages(state_messages[:-1]),
                definitions=registry.definitions,
                calls=siblings,
            ) // len(siblings)
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
        calls: tuple[ToolCall, ...],
    ) -> int:
        used = self.token_counter.count_model_input(
            messages,
            tools=definitions,
        ) + sum(
            self.token_counter.count_assistant_tool_call(call) for call in calls
        )
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


def _tool_protocol_violation(message: str) -> RuntimeTerminalError:
    """A tool call/result pair that the harness could not correlate.

    This is a runtime protocol fault, not an unavailable provider, so it keeps
    its own diagnosable code instead of collapsing into ``provider_call_failed``.
    """

    return RuntimeTerminalError(
        code="runtime_tool_protocol_violation",
        message=message,
        status_code=502,
    )


def _sibling_tool_calls(
    state_messages: Any,
    call: ToolCall,
) -> tuple[ToolCall, ...]:
    """Return every tool call declared by the assistant turn being executed.

    The budget for one turn's results has to be split across the calls that
    share it, so the split must be derived from the assistant message rather
    than from the single call this wrapper happens to be running.
    """

    terminal = state_messages[-1] if state_messages else None
    if not isinstance(terminal, AIMessage) or not terminal.tool_calls:
        return (call,)
    siblings = tuple(_runtime_tool_call(value) for value in terminal.tool_calls)
    return siblings if any(item.id == call.id for item in siblings) else (call,)


def _runtime_tool_call(value: object) -> ToolCall:
    if not isinstance(value, dict):
        raise _tool_protocol_violation("The model returned an unusable tool call.")
    call_id = value.get("id")
    name = value.get("name")
    arguments = value.get("args")
    if (
        not isinstance(call_id, str)
        or not call_id.strip()
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(arguments, dict)
    ):
        raise _tool_protocol_violation("The model returned an unusable tool call.")
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


def _different_message(left: AIMessageChunk, right: AIMessageChunk) -> bool:
    return bool(left.id and right.id and left.id != right.id)


def _chunk_text(chunk: AIMessageChunk) -> tuple[str, ...]:
    values: list[str] = []
    for block in chunk.content_blocks:
        if isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str) and text:
                values.append(text)
    return tuple(values)


def _chunk_reasoning(chunk: AIMessageChunk) -> str | None:
    values: list[str] = []
    for block in chunk.content_blocks:
        if isinstance(block, dict) and block.get("type") == "reasoning":
            reasoning = block.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                values.append(reasoning)
    if values:
        return "".join(values)
    value = chunk.additional_kwargs.get("reasoning_content")
    if isinstance(value, str):
        return value
    value = chunk.response_metadata.get("reasoning_content")
    return value if isinstance(value, str) else None


def _assistant_event(
    chunk: AIMessageChunk,
    *,
    reasoning_content: str | None,
    provider: str,
    model: str,
) -> AssistantMessageEvent:
    if chunk.invalid_tool_calls:
        # The provider stream produced tool call syntax LangChain could not
        # parse. Surface it as its own runtime code so it is never confused
        # with an unavailable provider.
        raise _tool_protocol_violation(
            "The model tool call could not be parsed from the provider stream."
        )
    calls: list[ToolCall] = []
    seen: set[str] = set()
    for value in chunk.tool_calls:
        call = _runtime_tool_call(value)
        if call.id in seen:
            raise _tool_protocol_violation(
                "The model reused one tool call id in a single turn."
            )
        seen.add(call.id)
        calls.append(call)
    content = _assistant_content(chunk)
    if calls and content == "":
        content = None
    if not (
        content is None
        or isinstance(content, str)
        or (
            isinstance(content, list)
            and all(isinstance(block, dict) for block in content)
        )
    ):
        raise TypeError("invalid assistant message content")
    return AssistantMessageEvent(
        content=content,
        tool_calls=tuple(calls),
        reasoning_content=reasoning_content,
        provider=provider,
        model=model,
    )


def _assistant_content(chunk: AIMessageChunk) -> object:
    content = chunk.content
    if isinstance(content, str) or content is None:
        return content
    if not isinstance(content, list):
        raise TypeError("invalid assistant message content")
    blocks: list[dict[str, object]] = []
    text_parts: list[str] = []
    only_text = True
    for raw_block in content:
        if isinstance(raw_block, str):
            text_parts.append(raw_block)
            blocks.append({"type": "text", "text": raw_block})
            continue
        if not isinstance(raw_block, dict):
            raise TypeError("invalid assistant message content")
        if raw_block.get("type") == "reasoning":
            continue
        block = dict(raw_block)
        blocks.append(block)
        if block.get("type") in {"text", "output_text"} and isinstance(
            block.get("text"), str
        ):
            text_parts.append(block["text"])
        else:
            only_text = False
    if not blocks:
        return None
    if only_text:
        return "".join(text_parts)
    return blocks
