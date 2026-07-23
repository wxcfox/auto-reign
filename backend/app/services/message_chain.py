from __future__ import annotations

from copy import deepcopy
import json

from app.services.json_safety import (
    MAX_JSON_CANONICAL_BYTES,
    MAX_JSON_STRING_CHARS,
    JsonSafetyError,
    canonical_json,
)
from app.services.runtime_types import AssistantMessageEvent, ToolCall, ToolResult


MAX_MESSAGE_CHAIN_MESSAGES = 256
MAX_MESSAGE_CHAIN_TOOL_CALLS = 128


def serialize_assistant_event(event: AssistantMessageEvent) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "assistant",
        "content": deepcopy(event.content),
        "model_info": {"provider": event.provider, "model": event.model},
        "compacted": event.compacted,
        "summary_compacted": event.summary_compacted,
        "compaction_version": event.compaction_version,
    }
    if event.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": _canonical_arguments(call.arguments),
                },
            }
            for call in event.tool_calls
        ]
    if event.reasoning_content is not None:
        message["reasoning_content"] = event.reasoning_content
    return message


def serialize_tool_result(
    call: ToolCall,
    result: ToolResult,
) -> dict[str, object]:
    if result.call_id != call.id:
        raise ValueError("messages_chain_tool_result_call_mismatch")
    message: dict[str, object] = {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": result.content,
    }
    if result.is_error:
        message["is_error"] = True
    return message


def validate_messages_chain(messages: object) -> list[dict[str, object]]:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages_chain_invalid_container")
    if len(messages) > MAX_MESSAGE_CHAIN_MESSAGES:
        raise ValueError("messages_chain_too_large")

    normalized: list[dict[str, object]] = []
    declared: dict[str, str] = {}
    unresolved: set[str] = set()
    resolved: set[str] = set()
    tool_call_count = 0

    for raw_message in messages:
        if not isinstance(raw_message, dict):
            raise ValueError("messages_chain_invalid_message")
        role = raw_message.get("role")
        if role == "assistant":
            if unresolved:
                raise ValueError("messages_chain_missing_tool_result")
            message = _normalize_assistant(raw_message)
            tool_calls = message.get("tool_calls", [])
            assert isinstance(tool_calls, list)
            for tool_call in tool_calls:
                tool_call_count += 1
                if tool_call_count > MAX_MESSAGE_CHAIN_TOOL_CALLS:
                    raise ValueError("messages_chain_too_large")
                assert isinstance(tool_call, dict)
                call_id = tool_call["id"]
                function = tool_call["function"]
                assert isinstance(call_id, str)
                assert isinstance(function, dict)
                if call_id in declared:
                    raise ValueError("messages_chain_duplicate_tool_call_id")
                name = function["name"]
                assert isinstance(name, str)
                declared[call_id] = name
                unresolved.add(call_id)
            normalized.append(message)
            continue
        if role == "tool":
            message = _normalize_tool(raw_message)
            call_id = message["tool_call_id"]
            name = message["name"]
            assert isinstance(call_id, str)
            assert isinstance(name, str)
            if call_id not in declared:
                raise ValueError("messages_chain_orphan_tool_result")
            if call_id in resolved:
                raise ValueError("messages_chain_duplicate_tool_result")
            if declared[call_id] != name:
                raise ValueError("messages_chain_tool_name_mismatch")
            resolved.add(call_id)
            unresolved.discard(call_id)
            normalized.append(message)
            continue
        raise ValueError("messages_chain_invalid_role")

    if unresolved:
        raise ValueError("messages_chain_missing_tool_result")
    try:
        canonical_json(normalized)
    except JsonSafetyError:
        raise ValueError("messages_chain_too_large") from None
    return normalized


def _normalize_assistant(raw: dict[object, object]) -> dict[str, object]:
    content = raw.get("content")
    if not _assistant_content_is_valid(content):
        raise ValueError("messages_chain_invalid_assistant_content")
    message: dict[str, object] = {
        "role": "assistant",
        "content": deepcopy(content),
    }

    raw_tool_calls = raw.get("tool_calls")
    if raw_tool_calls is not None:
        if not isinstance(raw_tool_calls, list):
            raise ValueError("messages_chain_invalid_tool_calls")
        tool_calls: list[dict[str, object]] = []
        for raw_call in raw_tool_calls:
            tool_calls.append(_normalize_tool_call(raw_call))
        if tool_calls:
            message["tool_calls"] = tool_calls

    reasoning = raw.get("reasoning_content")
    if reasoning is not None:
        if not isinstance(reasoning, str):
            raise ValueError("messages_chain_invalid_reasoning_content")
        message["reasoning_content"] = reasoning

    model_info = raw.get("model_info")
    if model_info is not None:
        if not isinstance(model_info, dict):
            raise ValueError("messages_chain_invalid_model_info")
        provider = model_info.get("provider")
        model = model_info.get("model")
        if provider is not None and not isinstance(provider, str):
            raise ValueError("messages_chain_invalid_model_info")
        if model is not None and not isinstance(model, str):
            raise ValueError("messages_chain_invalid_model_info")
        message["model_info"] = {"provider": provider, "model": model}

    for marker in ("compacted", "summary_compacted"):
        value = raw.get(marker)
        if value is not None:
            if type(value) is not bool:
                raise ValueError("messages_chain_invalid_compaction")
            message[marker] = value
    version = raw.get("compaction_version")
    if version is not None:
        if type(version) is not int or version < 1:
            raise ValueError("messages_chain_invalid_compaction")
        message["compaction_version"] = version
    elif "compaction_version" in raw:
        message["compaction_version"] = None
    return message


def _normalize_tool_call(raw_call: object) -> dict[str, object]:
    if not isinstance(raw_call, dict):
        raise ValueError("messages_chain_invalid_tool_call")
    call_id = raw_call.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("messages_chain_invalid_tool_call_id")
    if raw_call.get("type") != "function":
        raise ValueError("messages_chain_invalid_tool_call")
    function = raw_call.get("function")
    if not isinstance(function, dict):
        raise ValueError("messages_chain_invalid_tool_call")
    name = function.get("name")
    arguments = function.get("arguments")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("messages_chain_invalid_tool_name")
    if not isinstance(arguments, str):
        raise ValueError("messages_chain_invalid_tool_arguments")
    try:
        argument_bytes = len(arguments.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise ValueError("messages_chain_invalid_tool_arguments") from None
    if len(arguments) > MAX_JSON_STRING_CHARS or argument_bytes > MAX_JSON_CANONICAL_BYTES:
        raise ValueError("messages_chain_invalid_tool_arguments")
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise ValueError("messages_chain_invalid_tool_arguments") from None
    if not isinstance(parsed, dict) or _canonical_arguments(parsed) != arguments:
        raise ValueError("messages_chain_invalid_tool_arguments")
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _normalize_tool(raw: dict[object, object]) -> dict[str, object]:
    call_id = raw.get("tool_call_id")
    name = raw.get("name")
    content = raw.get("content")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("messages_chain_invalid_tool_result_id")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("messages_chain_invalid_tool_name")
    if not isinstance(content, str):
        raise ValueError("messages_chain_invalid_tool_content")
    message: dict[str, object] = {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
    }
    is_error = raw.get("is_error")
    if is_error is not None:
        if type(is_error) is not bool:
            raise ValueError("messages_chain_invalid_tool_error")
        message["is_error"] = is_error
    return message


def _canonical_arguments(arguments: dict[str, object]) -> str:
    try:
        return canonical_json(arguments)
    except JsonSafetyError:
        raise ValueError("messages_chain_invalid_tool_arguments") from None


def _assistant_content_is_valid(content: object) -> bool:
    if content is None or isinstance(content, str):
        candidate: object = content
    elif isinstance(content, list) and all(
        isinstance(block, dict) for block in content
    ):
        candidate = content
    else:
        return False
    try:
        canonical_json(candidate)
    except JsonSafetyError:
        return False
    return True
