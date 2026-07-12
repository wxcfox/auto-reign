from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import json

from app.core.errors import bad_request
from app.services.attachment_runtime_loader import (
    RuntimeAttachment,
    RuntimeAttachmentRef,
)
from app.services.runtime_types import RuntimeConversationTurn, ToolDefinition
from app.services.token_counter import RuntimeTokenCounter


@dataclass(frozen=True)
class ContextSelection:
    turns: tuple[RuntimeConversationTurn, ...]
    platform_prompt: str
    agent_prompt: str
    agents_md: str | None
    tool_definitions: tuple[ToolDefinition, ...]
    includes_attachments: bool
    upper_bound_tokens: int
    input_token_limit: int
    token_budget: int
    tool_result_token_reserve: int

class ContextAssembler:
    def __init__(
        self,
        *,
        token_budget: int,
        token_counter: RuntimeTokenCounter,
    ) -> None:
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        self.token_budget = token_budget
        self.token_counter = token_counter

    def select_turns(
        self,
        *,
        history: tuple[RuntimeConversationTurn, ...],
        base_system_prompt: str,
        attachment_system_prompt: str | None,
        agent_prompt: str = "",
        agents_md: str | None = None,
        tool_definitions: tuple[ToolDefinition, ...] = (),
        token_budget: int | None = None,
        tool_result_token_reserve: int = 0,
    ) -> ContextSelection:
        total_budget = (
            self.token_budget if token_budget is None else token_budget
        )
        if total_budget <= 0:
            raise ValueError("token_budget must be positive")
        reserve = tool_result_token_reserve if tool_definitions else 0
        if reserve < 0 or reserve >= total_budget:
            raise ValueError("invalid tool result token reserve")
        input_limit = total_budget - reserve

        selected_reversed: list[RuntimeConversationTurn] = []
        selected_count = self._count_selection(
            turns=(),
            platform_prompt=base_system_prompt,
            agent_prompt=agent_prompt,
            agents_md=agents_md,
            tool_definitions=tool_definitions,
        )
        includes_attachments = False

        for index, turn in enumerate(reversed(history)):
            candidate_reversed = [*selected_reversed, turn]
            candidate_turns = tuple(reversed(candidate_reversed))
            candidate_has_attachments = any(
                item.user.attachment_refs for item in candidate_turns
            )
            platform_prompt = base_system_prompt
            if candidate_has_attachments:
                if attachment_system_prompt is None:
                    raise ValueError("attachment system prompt is required")
                platform_prompt = attachment_system_prompt
            candidate_count = self._count_selection(
                turns=candidate_turns,
                platform_prompt=platform_prompt,
                agent_prompt=agent_prompt,
                agents_md=agents_md,
                tool_definitions=tool_definitions,
            )
            if candidate_count > input_limit:
                if index == 0:
                    raise bad_request(
                        "context_too_large",
                        "The current message exceeds the context budget.",
                    )
                break
            selected_reversed.append(turn)
            selected_count = candidate_count
            includes_attachments = candidate_has_attachments

        selected = tuple(reversed(selected_reversed))
        platform_prompt = (
            attachment_system_prompt
            if includes_attachments
            else base_system_prompt
        )
        if platform_prompt is None:
            raise ValueError("selected platform prompt is required")
        if selected_count > input_limit:
            raise bad_request(
                "context_too_large",
                "The conversation context is too large.",
            )
        return ContextSelection(
            turns=selected,
            platform_prompt=platform_prompt,
            agent_prompt=agent_prompt,
            agents_md=agents_md,
            tool_definitions=tool_definitions,
            includes_attachments=includes_attachments,
            upper_bound_tokens=selected_count,
            input_token_limit=input_limit,
            token_budget=total_budget,
            tool_result_token_reserve=reserve,
        )

    def render_selected(
        self,
        selection: ContextSelection,
        *,
        attachments: Mapping[str, RuntimeAttachment],
    ) -> list[dict[str, object]]:
        messages = _render_messages(
            platform_prompt=selection.platform_prompt,
            agent_prompt=selection.agent_prompt,
            agents_md=selection.agents_md,
            turns=selection.turns,
            attachments=attachments,
            use_attachment_upper_bounds=False,
        )
        actual_tokens = self.token_counter.count_model_input(
            messages,
            tools=selection.tool_definitions,
        )
        if actual_tokens > selection.input_token_limit:
            raise bad_request(
                "context_too_large",
                "The conversation context is too large.",
            )
        return messages

    def _count_selection(
        self,
        *,
        turns: tuple[RuntimeConversationTurn, ...],
        platform_prompt: str,
        agent_prompt: str,
        agents_md: str | None,
        tool_definitions: tuple[ToolDefinition, ...],
    ) -> int:
        messages = _render_messages(
            platform_prompt=platform_prompt,
            agent_prompt=agent_prompt,
            agents_md=agents_md,
            turns=turns,
            attachments={},
            use_attachment_upper_bounds=True,
        )
        return self.token_counter.count_model_input(
            messages,
            tools=tool_definitions,
        )


def _render_messages(
    *,
    platform_prompt: str,
    agent_prompt: str,
    agents_md: str | None,
    turns: tuple[RuntimeConversationTurn, ...],
    attachments: Mapping[str, RuntimeAttachment],
    use_attachment_upper_bounds: bool,
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": platform_prompt}
    ]
    if agent_prompt:
        messages.append({"role": "system", "content": agent_prompt})
    if agents_md:
        messages.append({"role": "system", "content": agents_md})

    for turn in turns:
        refs = turn.user.attachment_refs
        if refs:
            blocks: list[dict[str, object]] = [
                {"type": "text", "text": turn.user.text}
            ]
            for ref in refs:
                if use_attachment_upper_bounds:
                    blocks.append(_attachment_upper_bound_block(ref))
                    continue
                attachment = attachments.get(ref.id)
                if attachment is None:
                    raise ValueError("selected attachment was not loaded")
                if attachment.text is not None:
                    blocks.append(
                        {
                            "type": "text",
                            "text": attachment_text_block(attachment),
                        }
                    )
                else:
                    blocks.append(image_content_block(attachment))
            messages.append({"role": "user", "content": blocks})
        else:
            messages.append({"role": "user", "content": turn.user.text})
        messages.extend(
            {"role": "assistant", "content": assistant.text}
            for assistant in turn.assistants
        )
    return messages


def _attachment_upper_bound_block(ref: RuntimeAttachmentRef) -> dict[str, object]:
    if ref.media_type.startswith("image/"):
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{ref.media_type};base64,"},
        }
    parsed_size = max(ref.parsed_size_bytes or 0, 0)
    placeholder = RuntimeAttachment(
        id=ref.id,
        filename=ref.filename,
        media_type=ref.media_type,
        text="\x00" * parsed_size,
        image_bytes=None,
    )
    return {"type": "text", "text": attachment_text_block(placeholder)}


def attachment_text_block(attachment: RuntimeAttachment) -> str:
    if attachment.text is None or attachment.image_bytes is not None:
        raise ValueError("text attachment content is required")
    metadata = json.dumps(
        {
            "id": attachment.id,
            "filename": attachment.filename,
            "media_type": attachment.media_type,
            "trust": "untrusted",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"<attachment>\nmetadata={metadata}\n{attachment.text}\n</attachment>"


def image_content_block(attachment: RuntimeAttachment) -> dict[str, object]:
    if attachment.image_bytes is None or attachment.text is not None:
        raise ValueError("image attachment content is required")
    encoded = base64.b64encode(attachment.image_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{attachment.media_type};base64,{encoded}"
        },
    }
