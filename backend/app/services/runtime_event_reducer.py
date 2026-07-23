from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
import json
from typing import Literal

from app.services.chat_blocks import (
    TextBlock,
    ToolBlock,
    append_text_block,
    apply_tool_result,
    create_text_block,
    create_tool_block,
    update_tool_block,
)
from app.services.json_safety import (
    MAX_JSON_STRING_CHARS,
    JsonSafetyError,
    canonical_json,
)
from app.services.message_chain import (
    serialize_assistant_event,
    serialize_tool_result,
    validate_messages_chain,
)
from app.services.runtime_types import (
    AssistantContent,
    AssistantMessageEvent,
    RuntimeEvent,
    TextDeltaEvent,
    ToolCall,
    ToolResultEvent,
    ToolStartEvent,
)
from app.services.text_offsets import TextOffsetError, advance_utf16_offset


MAX_RESULT_SOURCES = 20
MAX_TERMINATION_REASON_CHARS = 128
_SUCCESS_KEYS = frozenset(
    {
        "value",
        "messages_chain",
        "blocks",
        "context_compactions",
        "sources",
        "termination_reason",
    }
)
_PARTIAL_KEYS = frozenset(
    {"value", "blocks", "context_compactions", "sources", "termination_reason"}
)


class RuntimeEventReductionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ResultEnvelopeError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


ReducerEmissionType = Literal["chunk", "block_created", "block_updated"]


@dataclass(frozen=True, slots=True)
class ReducerEmission:
    type: ReducerEmissionType
    data: dict[str, object]


class RuntimeEventReducer:
    def __init__(self, *, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self.messages_chain: list[dict[str, object]] = []
        self.blocks: list[TextBlock | ToolBlock] = []
        self.context_compactions: list[dict[str, object]] = []
        self.sources: list[object] = []
        self.termination_reason: str | None = None
        self._declared: dict[str, ToolCall] = {}
        self._started: set[str] = set()
        self._resolved: set[str] = set()
        self._tool_block_indexes: dict[str, int] = {}
        self._text_block_index: int | None = None
        self._pending_deltas: list[str] = []
        self._value_parts: list[str] = []
        self._value_offset = 0
        self._text_block_offset = 0

    def accept(self, event: RuntimeEvent) -> tuple[ReducerEmission, ...]:
        if isinstance(event, TextDeltaEvent):
            return self._accept_delta(event)
        if isinstance(event, AssistantMessageEvent):
            return self._accept_assistant(event)
        if isinstance(event, ToolStartEvent):
            return self._accept_tool_start(event)
        if isinstance(event, ToolResultEvent):
            return self._accept_tool_result(event)
        raise RuntimeEventReductionError("runtime_event_invalid")

    def finish_success(self) -> dict[str, object]:
        final_answer = self._success_terminal_answer()
        if self._pending_deltas:
            raise RuntimeEventReductionError("runtime_event_text_mismatch")
        if self._unresolved_calls():
            raise RuntimeEventReductionError("runtime_event_unresolved_tool_call")
        if any(
            block["type"] == "tool" and block["status"] not in {"done", "error"}
            for block in self.blocks
        ):
            raise RuntimeEventReductionError("runtime_event_pending_tool_block")
        chain = validate_messages_chain(self.messages_chain)
        result: dict[str, object] = {
            "value": final_answer,
            "messages_chain": chain,
            "blocks": deepcopy(self.blocks),
            "context_compactions": deepcopy(self.context_compactions),
            "sources": deepcopy(self.sources),
            "termination_reason": self.termination_reason,
        }
        validate_result_envelope(result, success=True)
        return result

    def finish(self) -> dict[str, object]:
        return self.finish_success()

    def partial_result(self) -> dict[str, object]:
        blocks = _terminal_partial_blocks(self.blocks)
        value = "".join(self._value_parts)[:MAX_JSON_STRING_CHARS]
        result: dict[str, object] = {
            "value": value,
            "blocks": blocks,
            "context_compactions": [],
            "sources": [],
            "termination_reason": None,
        }
        while True:
            try:
                validate_result_envelope(result, success=False)
                return result
            except ResultEnvelopeError:
                if not blocks:
                    result["value"] = ""
                    validate_result_envelope(result, success=False)
                    return result
                blocks.pop()

    def _accept_delta(self, event: TextDeltaEvent) -> tuple[ReducerEmission, ...]:
        if not isinstance(event.content, str):
            raise RuntimeEventReductionError("runtime_event_invalid")
        if not event.content:
            return ()
        if self._unresolved_calls():
            raise RuntimeEventReductionError("runtime_event_unresolved_tool_call")
        emissions: list[ReducerEmission] = []
        if self._text_block_index is None:
            self._text_block_index = len(self.blocks)
            self._text_block_offset = 0
            block = create_text_block()
            self.blocks.append(block)
            emissions.append(
                ReducerEmission("block_created", {"block": deepcopy(block)})
            )
        block = self.blocks[self._text_block_index]
        if block["type"] != "text":
            raise RuntimeEventReductionError("runtime_event_invalid")
        block_offset = self._text_block_offset
        offset = self._value_offset
        try:
            next_block_offset = advance_utf16_offset(block_offset, event.content)
            next_value_offset = advance_utf16_offset(offset, event.content)
        except TextOffsetError:
            raise RuntimeEventReductionError("runtime_event_invalid") from None
        updated = append_text_block(block, event.content)
        self.blocks[self._text_block_index] = updated
        self._pending_deltas.append(event.content)
        self._value_parts.append(event.content)
        self._text_block_offset = next_block_offset
        self._value_offset = next_value_offset
        emissions.append(
            ReducerEmission(
                "chunk",
                {
                    "block_id": updated["id"],
                    "block_offset": block_offset,
                    "offset": offset,
                    "content": event.content,
                },
            )
        )
        return tuple(emissions)

    def _accept_assistant(
        self,
        event: AssistantMessageEvent,
    ) -> tuple[ReducerEmission, ...]:
        if self._unresolved_calls():
            raise RuntimeEventReductionError("runtime_event_unresolved_tool_call")
        visible = _assistant_visible_text(event.content)
        if "".join(self._pending_deltas) != visible:
            raise RuntimeEventReductionError("runtime_event_text_mismatch")
        emissions: list[ReducerEmission] = []
        if self._text_block_index is not None:
            block = self.blocks[self._text_block_index]
            if block["type"] != "text":
                raise RuntimeEventReductionError("runtime_event_invalid")
            completed = append_text_block(block, "", status="done")
            self.blocks[self._text_block_index] = completed
            emissions.append(
                ReducerEmission("block_updated", {"block": deepcopy(completed)})
            )
        self._text_block_index = None
        self._text_block_offset = 0
        self._pending_deltas = []

        normalized = _normalized_assistant(
            event,
            provider=self.provider,
            model=self.model,
        )
        normalized = replace(
            normalized,
            tool_calls=tuple(_copy_tool_call(call) for call in normalized.tool_calls),
        )
        for call in normalized.tool_calls:
            if call.id in self._declared:
                raise RuntimeEventReductionError("runtime_event_duplicate_tool_call")
            self._declared[call.id] = call
        self.messages_chain.append(serialize_assistant_event(normalized))
        if (
            normalized.compacted
            or normalized.summary_compacted
            or normalized.compaction_version is not None
        ):
            self.context_compactions.append(
                {
                    "message_index": len(self.messages_chain) - 1,
                    "compacted": normalized.compacted,
                    "summary_compacted": normalized.summary_compacted,
                    "version": normalized.compaction_version,
                }
            )
        return tuple(emissions)

    def _accept_tool_start(
        self,
        event: ToolStartEvent,
    ) -> tuple[ReducerEmission, ...]:
        declared = self._declared.get(event.call.id)
        if declared is None:
            raise RuntimeEventReductionError("runtime_event_orphan_tool_start")
        if event.call.id in self._started:
            raise RuntimeEventReductionError("runtime_event_duplicate_tool_start")
        if declared != event.call:
            raise RuntimeEventReductionError("runtime_event_tool_start_mismatch")
        self._started.add(event.call.id)
        block = create_tool_block(event.call)
        self._tool_block_indexes[event.call.id] = len(self.blocks)
        self.blocks.append(block)
        return (ReducerEmission("block_created", {"block": deepcopy(block)}),)

    def _accept_tool_result(
        self,
        event: ToolResultEvent,
    ) -> tuple[ReducerEmission, ...]:
        declared = self._declared.get(event.call.id)
        if declared is None or event.call.id not in self._started:
            raise RuntimeEventReductionError("runtime_event_orphan_tool_result")
        if event.call.id in self._resolved:
            raise RuntimeEventReductionError("runtime_event_duplicate_tool_result")
        if declared != event.call or event.result.call_id != event.call.id:
            raise RuntimeEventReductionError("runtime_event_tool_result_mismatch")
        block_index = self._tool_block_indexes[event.call.id]
        block = self.blocks[block_index]
        if block["type"] != "tool":
            raise RuntimeEventReductionError("runtime_event_invalid")
        completed = apply_tool_result(block, event.result)
        self.blocks[block_index] = completed
        self._resolved.add(event.call.id)
        self.messages_chain.append(serialize_tool_result(event.call, event.result))
        self._collect_metadata(event.result.metadata)
        return (ReducerEmission("block_updated", {"block": deepcopy(completed)}),)

    def _collect_metadata(self, metadata: object) -> None:
        if not isinstance(metadata, dict):
            return
        raw_sources = metadata.get("sources")
        if isinstance(raw_sources, list):
            for source in raw_sources:
                if len(self.sources) >= MAX_RESULT_SOURCES:
                    break
                if not isinstance(source, dict):
                    continue
                try:
                    self.sources.append(json.loads(canonical_json(source)))
                except (JsonSafetyError, ValueError, TypeError):
                    continue
        reason = metadata.get("termination_reason")
        if (
            isinstance(reason, str)
            and 0 < len(reason) <= MAX_TERMINATION_REASON_CHARS
            and not any(ord(character) < 32 or ord(character) == 127 for character in reason)
        ):
            self.termination_reason = reason

    def _unresolved_calls(self) -> set[str]:
        return set(self._declared).difference(self._resolved)

    def _success_terminal_answer(self) -> str:
        if not self.messages_chain:
            raise RuntimeEventReductionError("runtime_event_invalid_terminal")
        terminal = self.messages_chain[-1]
        if terminal.get("role") != "assistant" or terminal.get("tool_calls"):
            raise RuntimeEventReductionError("runtime_event_invalid_terminal")
        content = terminal.get("content")
        if not (content is None or isinstance(content, str | list)):
            raise RuntimeEventReductionError("runtime_event_invalid_terminal")
        answer = _assistant_visible_text(content)
        if not answer.strip():
            raise RuntimeEventReductionError("runtime_event_invalid_terminal")
        return answer


def validate_result_envelope(result: object, *, success: bool) -> None:
    expected = _SUCCESS_KEYS if success else _PARTIAL_KEYS
    if not isinstance(result, dict) or set(result) != expected:
        raise ResultEnvelopeError("runtime_result_invalid")
    try:
        canonical_json(result)
    except JsonSafetyError as error:
        raise ResultEnvelopeError("runtime_result_too_large") from error


def _normalized_assistant(
    event: AssistantMessageEvent,
    *,
    provider: str,
    model: str,
) -> AssistantMessageEvent:
    if event.provider not in {None, provider} or event.model not in {None, model}:
        raise RuntimeEventReductionError("runtime_event_model_mismatch")
    return replace(event, provider=provider, model=model)


def _copy_tool_call(call: ToolCall) -> ToolCall:
    if not isinstance(call, ToolCall):
        raise RuntimeEventReductionError("runtime_event_invalid")
    try:
        arguments = json.loads(canonical_json(call.arguments))
    except (JsonSafetyError, ValueError, TypeError):
        raise RuntimeEventReductionError("runtime_event_invalid") from None
    if not isinstance(arguments, dict):
        raise RuntimeEventReductionError("runtime_event_invalid")
    return ToolCall(id=call.id, name=call.name, arguments=arguments)


def _assistant_visible_text(content: AssistantContent) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise RuntimeEventReductionError("runtime_event_invalid")
    values: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            raise RuntimeEventReductionError("runtime_event_invalid")
        if block.get("type") not in {"text", "output_text"}:
            continue
        text = block.get("text")
        if not isinstance(text, str):
            raise RuntimeEventReductionError("runtime_event_invalid")
        values.append(text)
    return "".join(values)


def _terminal_partial_blocks(
    blocks: Sequence[TextBlock | ToolBlock],
) -> list[TextBlock | ToolBlock]:
    terminal: list[TextBlock | ToolBlock] = []
    for raw in blocks:
        block = deepcopy(raw)
        if block["type"] == "text" and block["status"] != "done":
            block = append_text_block(block, "", status="done")
        elif block["type"] == "tool" and block["status"] not in {"done", "error"}:
            block = update_tool_block(
                block,
                tool_output={"code": "execution_interrupted"},
                status="error",
            )
        terminal.append(block)
    return terminal
