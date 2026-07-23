import json
import logging
import math
from collections.abc import Callable, Iterator
import re
import time
from typing import Any

from fastapi import HTTPException
from openai import OpenAI

from app.core.config import Settings, get_settings
from app.core.errors import bad_gateway, service_unavailable
from app.core.limits import MAX_PROVIDER_TOKEN_COUNT
from app.core.model_providers import find_chat_provider
from app.services.runtime_types import (
    ProviderCallMetrics,
    ProviderReasoningDelta,
    RuntimeObserver,
    ToolCall,
    ToolDefinition,
)

logger = logging.getLogger(__name__)

_DATA_IMAGE_URL = re.compile(
    r"^data:(image/[a-z0-9][a-z0-9.+-]*);base64,([A-Za-z0-9+/]+={0,2})$"
)
_SAFE_PROVIDER_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")


class ModelService:
    def __init__(
        self,
        settings: Settings | None = None,
        client_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client_factory = client_factory or OpenAI
        self.clock = clock or time.monotonic

    def stream_turn(
        self,
        messages: list[dict[str, object]],
        *,
        provider: str,
        model: str,
        call_index: int,
        observer: RuntimeObserver,
        tools: tuple[ToolDefinition, ...] | None = None,
    ) -> Iterator[str | ToolCall | ProviderReasoningDelta]:
        if isinstance(call_index, bool) or not isinstance(call_index, int) or call_index < 1:
            raise ValueError("provider call index must be a positive integer")
        if not callable(observer):
            raise ValueError("runtime observer is required")
        normalized_messages = self._validate_messages(messages)
        normalized_tools = self._validate_tools(tools)
        resolved_provider, resolved_model, api_key, base_url = self._resolve_provider(
            provider,
            model,
        )
        started = self._read_clock()
        stream: object | None = None
        provider_request_id: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        first_token_latency_ms: float | None = None
        status = "failed"
        try:
            client = self.client_factory(
                api_key=api_key,
                base_url=base_url,
                timeout=self.settings.model_request_timeout_seconds,
                max_retries=0,
            )
            request: dict[str, object] = {
                "model": resolved_model,
                "messages": normalized_messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if normalized_tools:
                request["tools"] = self._tool_payloads(normalized_tools)
            stream = client.chat.completions.create(**request)
            provider_request_id = self._provider_request_id(stream)

            yielded_text = False
            tool_index: int | None = None
            tool_id_parts: list[str] = []
            tool_name_parts: list[str] = []
            tool_argument_parts: list[str] = []
            for chunk in stream:
                usage = self._field(chunk, "usage")
                observed_input = self._safe_token_count(
                    self._field(usage, "prompt_tokens")
                )
                observed_output = self._safe_token_count(
                    self._field(usage, "completion_tokens")
                )
                if observed_input is not None:
                    input_tokens = observed_input
                if observed_output is not None:
                    output_tokens = observed_output
                for choice in self._field(chunk, "choices") or ():
                    delta = self._field(choice, "delta")
                    content = self._field(delta, "content")
                    reasoning_parts, text_parts = self._content_delta_parts(content)
                    direct_reasoning = self._field(delta, "reasoning_content")
                    if direct_reasoning is None:
                        direct_reasoning = self._field(delta, "reasoning")
                    if direct_reasoning is not None:
                        if not isinstance(direct_reasoning, str):
                            raise ValueError("invalid model reasoning delta")
                        if direct_reasoning:
                            reasoning_parts.insert(0, direct_reasoning)
                    for reasoning in reasoning_parts:
                        if first_token_latency_ms is None:
                            first_token_latency_ms = self._elapsed_milliseconds(started)
                        yield ProviderReasoningDelta(content=reasoning)
                    for text in text_parts:
                        if tool_index is not None:
                            raise ValueError("mixed model stream")
                        if first_token_latency_ms is None:
                            first_token_latency_ms = self._elapsed_milliseconds(
                                started
                            )
                        yielded_text = True
                        yield text

                    tool_calls = self._field(delta, "tool_calls")
                    if tool_calls is None or tool_calls == []:
                        continue
                    if yielded_text:
                        raise ValueError("mixed model stream")
                    if not isinstance(tool_calls, (list, tuple)) or len(tool_calls) != 1:
                        raise ValueError("multiple model tool calls")
                    tool_delta = tool_calls[0]
                    index = self._field(tool_delta, "index")
                    if isinstance(index, bool) or not isinstance(index, int) or index != 0:
                        raise ValueError("invalid model tool call index")
                    if tool_index is None:
                        tool_index = index
                    elif index != tool_index:
                        raise ValueError("multiple model tool calls")

                    call_type = self._field(tool_delta, "type")
                    if call_type is not None and call_type != "function":
                        raise ValueError("invalid model tool call type")
                    self._append_fragment(
                        tool_id_parts,
                        self._field(tool_delta, "id"),
                    )
                    function = self._field(tool_delta, "function")
                    if function is not None:
                        self._append_fragment(
                            tool_name_parts,
                            self._field(function, "name"),
                        )
                        self._append_fragment(
                            tool_argument_parts,
                            self._field(function, "arguments"),
                        )

            if tool_index is not None:
                call_id = "".join(tool_id_parts)
                name = "".join(tool_name_parts)
                raw_arguments = "".join(tool_argument_parts)
                if not call_id or not name or not raw_arguments:
                    raise ValueError("incomplete model tool call")
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("model tool arguments must be an object")
                status = "completed"
                yield ToolCall(id=call_id, name=name, arguments=arguments)
                return
            if not yielded_text:
                raise ValueError("empty model stream")
            status = "completed"
        except Exception as error:
            if provider_request_id is None:
                provider_request_id = self._provider_request_id(error)
            logger.error(
                "provider_stream_failed",
                extra={
                    "provider": resolved_provider,
                    "model": resolved_model,
                    "exception_type": type(error).__name__,
                    "error_code": "provider_call_failed",
                },
                exc_info=False,
            )
            raise bad_gateway(
                "provider_call_failed",
                f"The {resolved_provider} model request failed.",
            ) from error
        finally:
            self._close_stream(stream)
            unavailable_fields = tuple(
                field
                for field, value in (
                    ("provider_request_id", provider_request_id),
                    ("input_tokens", input_tokens),
                    ("output_tokens", output_tokens),
                    ("first_token_latency_ms", first_token_latency_ms),
                )
                if value is None
            )
            self._notify_observer(
                observer,
                ProviderCallMetrics(
                    call_index=call_index,
                    provider=resolved_provider,
                    model=resolved_model,
                    provider_request_id=provider_request_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    first_token_latency_ms=first_token_latency_ms,
                    duration_ms=self._elapsed_milliseconds(started),
                    status=status,
                    unavailable_fields=unavailable_fields,
                ),
            )

    @staticmethod
    def _validate_messages(
        messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if not isinstance(messages, list) or not messages:
            raise ValueError("validated chat messages are required")
        normalized: list[dict[str, object]] = []
        for item in messages:
            if not isinstance(item, dict):
                raise ValueError("validated chat messages are required")
            role = item.get("role")
            content = item.get("content")
            if not isinstance(role, str):
                raise ValueError("validated chat messages are required")
            if role == "tool":
                normalized.append(ModelService._validate_tool_message(item))
                continue
            if role not in {"system", "user", "assistant"}:
                raise ValueError("validated chat messages are required")
            if role == "assistant" and "tool_calls" in item:
                normalized.append(
                    ModelService._validate_assistant_tool_message(item)
                )
                continue
            if isinstance(content, str) and content:
                normalized.append({"role": role, "content": content})
                continue
            if role != "user" or not isinstance(content, list) or not content:
                raise ValueError("validated chat messages are required")
            normalized.append(
                {
                    "role": role,
                    "content": [
                        ModelService._validate_content_block(block)
                        for block in content
                    ],
                }
            )
        return normalized

    @staticmethod
    def _validate_assistant_tool_message(
        item: dict[str, object],
    ) -> dict[str, object]:
        if item.get("content") is not None:
            raise ValueError("validated chat messages are required")
        tool_calls = item.get("tool_calls")
        if not isinstance(tool_calls, list) or len(tool_calls) != 1:
            raise ValueError("validated chat messages are required")
        call = tool_calls[0]
        if not isinstance(call, dict):
            raise ValueError("validated chat messages are required")
        call_id = call.get("id")
        if (
            not isinstance(call_id, str)
            or not call_id
            or call.get("type") != "function"
        ):
            raise ValueError("validated chat messages are required")
        function = call.get("function")
        if not isinstance(function, dict):
            raise ValueError("validated chat messages are required")
        name = function.get("name")
        raw_arguments = function.get("arguments")
        if not isinstance(name, str) or not name or not isinstance(raw_arguments, str):
            raise ValueError("validated chat messages are required")
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, ValueError) as error:
            raise ValueError("validated chat messages are required") from error
        if not isinstance(arguments, dict):
            raise ValueError("validated chat messages are required")
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": raw_arguments,
                    },
                }
            ],
        }

    @staticmethod
    def _validate_tool_message(item: dict[str, object]) -> dict[str, object]:
        call_id = item.get("tool_call_id")
        content = item.get("content")
        if not isinstance(call_id, str) or not call_id or not isinstance(content, str):
            raise ValueError("validated chat messages are required")
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": content,
        }

    @staticmethod
    def _validate_tools(
        tools: tuple[ToolDefinition, ...] | None,
    ) -> tuple[ToolDefinition, ...]:
        if tools is None:
            return ()
        if not isinstance(tools, tuple):
            raise ValueError("validated tool definitions are required")
        names: set[str] = set()
        for definition in tools:
            if (
                not isinstance(definition, ToolDefinition)
                or not definition.name
                or not definition.description
                or not isinstance(definition.input_schema, dict)
                or definition.name in names
            ):
                raise ValueError("validated tool definitions are required")
            names.add(definition.name)
        return tools

    @staticmethod
    def _tool_payloads(
        tools: tuple[ToolDefinition, ...],
    ) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.input_schema,
                },
            }
            for definition in tools
        ]

    @staticmethod
    def _field(value: object, name: str) -> object:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    @staticmethod
    def _provider_request_id(stream: object) -> str | None:
        response = getattr(stream, "response", None)
        headers = getattr(response, "headers", None)
        getter = getattr(headers, "get", None)
        if not callable(getter):
            return None
        try:
            value = getter("x-request-id")
        except Exception:
            return None
        if isinstance(value, str) and _SAFE_PROVIDER_REQUEST_ID.fullmatch(value):
            return value
        return None

    @staticmethod
    def _safe_token_count(value: object) -> int | None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > MAX_PROVIDER_TOKEN_COUNT
        ):
            return None
        return value

    def _elapsed_milliseconds(self, started: object) -> float:
        try:
            seconds = self._read_clock() - started  # type: ignore[operator]
        except (OverflowError, TypeError, ValueError):
            return 0.0
        return self._milliseconds(seconds)

    def _read_clock(self) -> object:
        try:
            return self.clock()
        except Exception:
            return 0.0

    @staticmethod
    def _milliseconds(seconds: object) -> float:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return 0.0
        try:
            converted = float(seconds)
        except (OverflowError, TypeError, ValueError):
            return 0.0
        if not math.isfinite(converted) or converted <= 0:
            return 0.0
        milliseconds = converted * 1_000
        if not math.isfinite(milliseconds):
            return 0.0
        return round(milliseconds, 2)

    @staticmethod
    def _close_stream(stream: object | None) -> None:
        close = getattr(stream, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:
            # The response lifecycle is already terminal. Provider success or
            # failure remains determined by stream consumption, not cleanup.
            return

    @staticmethod
    def _notify_observer(
        observer: RuntimeObserver,
        metrics: ProviderCallMetrics,
    ) -> None:
        try:
            observer(metrics)
        except Exception as error:
            logger.error(
                "provider_observer_failed",
                extra={
                    "provider": metrics.provider,
                    "model": metrics.model,
                    "exception_type": type(error).__name__,
                    "error_code": "provider_observer_failed",
                },
                exc_info=False,
            )

    @staticmethod
    def _append_fragment(parts: list[str], fragment: object) -> None:
        if fragment is None:
            return
        if not isinstance(fragment, str):
            raise ValueError("invalid model tool call fragment")
        parts.append(fragment)

    @classmethod
    def _content_delta_parts(
        cls,
        content: object,
    ) -> tuple[list[str], list[str]]:
        if content is None or content == "":
            return [], []
        if isinstance(content, str):
            return [], [content]
        if not isinstance(content, (list, tuple)):
            raise ValueError("invalid model content delta")
        reasoning: list[str] = []
        text: list[str] = []
        for block in content:
            block_type = cls._field(block, "type")
            if block_type == "reasoning":
                value = cls._field(block, "reasoning")
                if not isinstance(value, str):
                    raise ValueError("invalid model reasoning delta")
                if value:
                    reasoning.append(value)
                continue
            if block_type in {"text", "output_text"}:
                value = cls._field(block, "text")
                if not isinstance(value, str):
                    raise ValueError("invalid model content delta")
                if value:
                    text.append(value)
                continue
            raise ValueError("invalid model content delta")
        return reasoning, text

    @staticmethod
    def _validate_content_block(block: object) -> dict[str, object]:
        if not isinstance(block, dict):
            raise ValueError("validated chat messages are required")
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if set(block) != {"type", "text"} or not isinstance(text, str) or not text:
                raise ValueError("validated chat messages are required")
            return {"type": "text", "text": text}
        if block_type != "image_url" or set(block) != {"type", "image_url"}:
            raise ValueError("validated chat messages are required")
        image_url = block.get("image_url")
        if not isinstance(image_url, dict) or set(image_url) != {"url"}:
            raise ValueError("validated chat messages are required")
        url = image_url.get("url")
        if not isinstance(url, str):
            raise ValueError("validated chat messages are required")
        matched = _DATA_IMAGE_URL.fullmatch(url)
        if matched is None or len(matched.group(2)) % 4 != 0:
            raise ValueError("validated chat messages are required")
        return {"type": "image_url", "image_url": {"url": url}}

    def _resolve_provider(
        self,
        provider: str,
        model: str,
    ) -> tuple[str, str, str, str | None]:
        if not isinstance(provider, str) or not provider:
            raise self._model_unavailable()
        if not isinstance(model, str) or not model:
            raise self._model_unavailable()
        provider_config = find_chat_provider(self.settings, provider)
        if provider_config is None or provider_config.api_key is None:
            raise self._model_unavailable()
        if model not in provider_config.models:
            raise self._model_unavailable()
        return (
            provider_config.name,
            model,
            provider_config.api_key,
            provider_config.base_url,
        )

    @staticmethod
    def _model_unavailable() -> HTTPException:
        return service_unavailable(
            "model_unavailable",
            "The selected model is unavailable.",
        )
