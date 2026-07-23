from __future__ import annotations

from dataclasses import dataclass
import json

from app.core.errors import bad_request
from app.services.runtime_types import (
    RuntimeTaskTurn,
    RuntimeImageContext,
    RuntimeSelectedDocumentsContext,
    RuntimeTextContext,
    RuntimeUserTurn,
    ToolDefinition,
)
from app.services.token_counter import RuntimeTokenCounter


@dataclass(frozen=True)
class ContextSelection:
    turns: tuple[RuntimeTaskTurn, ...]
    platform_prompt: str
    agent_prompt: str
    agents_md: str | None
    tool_definitions: tuple[ToolDefinition, ...]
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
        history: tuple[RuntimeTaskTurn, ...],
        base_system_prompt: str,
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

        selected_reversed: list[RuntimeTaskTurn] = []
        selected_count = self._count_selection(
            turns=(),
            platform_prompt=base_system_prompt,
            agent_prompt=agent_prompt,
            agents_md=agents_md,
            tool_definitions=tool_definitions,
        )
        for index, turn in enumerate(reversed(history)):
            candidate_reversed = [*selected_reversed, turn]
            candidate_turns = tuple(reversed(candidate_reversed))
            candidate_count = self._count_selection(
                turns=candidate_turns,
                platform_prompt=base_system_prompt,
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

        selected = tuple(reversed(selected_reversed))
        platform_prompt = base_system_prompt
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
            upper_bound_tokens=selected_count,
            input_token_limit=input_limit,
            token_budget=total_budget,
            tool_result_token_reserve=reserve,
        )

    def render_selected(
        self,
        selection: ContextSelection,
    ) -> list[dict[str, object]]:
        messages = _render_messages(
            platform_prompt=selection.platform_prompt,
            agent_prompt=selection.agent_prompt,
            agents_md=selection.agents_md,
            turns=selection.turns,
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
        turns: tuple[RuntimeTaskTurn, ...],
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
    turns: tuple[RuntimeTaskTurn, ...],
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": platform_prompt}
    ]
    if agent_prompt:
        messages.append({"role": "system", "content": agent_prompt})
    if agents_md:
        messages.append({"role": "system", "content": agents_md})

    for turn in turns:
        messages.append({"role": "user", "content": _render_user(turn.user)})
        messages.extend(
            {"role": "assistant", "content": assistant.text}
            for assistant in turn.assistants
        )
    return messages


def _render_user(user: RuntimeUserTurn) -> object:
    renderable = tuple(
        context
        for context in user.contexts
        if not isinstance(context, RuntimeSelectedDocumentsContext)
    )
    if not renderable:
        return user.text

    content: list[dict[str, object]] = [
        {"type": "text", "text": user.text},
    ]
    for context in renderable:
        if isinstance(context, RuntimeTextContext):
            content.append(
                {
                    "type": "text",
                    "text": _context_text(context),
                }
            )
        elif isinstance(context, RuntimeImageContext):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            f"data:{context.mime_type};base64,"
                            f"{context.image_base64}"
                        )
                    },
                }
            )
    return content


def _context_text(context: RuntimeTextContext) -> str:
    source = (
        "chat attachment"
        if context.source_type == "attachment"
        else "knowledge retrieval"
    )
    metadata = json.dumps(
        {"source": source, "name": context.name},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    metadata_bytes = len(metadata.encode("utf-8"))
    content_bytes = len(context.text.encode("utf-8"))
    return (
        "[UNTRUSTED_CONTEXT "
        f"metadata_utf8_bytes={metadata_bytes} "
        f"content_utf8_bytes={content_bytes}]\n"
        f"{metadata}\n"
        f"{context.text}\n"
        "[END_UNTRUSTED_CONTEXT]"
    )
