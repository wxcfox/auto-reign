from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
import json

from app.core.limits import (
    DEFAULT_RUNTIME_MAX_TOOL_ROUNDS,
    MAX_RUNTIME_MAX_TOOL_ROUNDS,
)
from app.services.agent_home_service import AgentHomeService, WorkspaceUnavailable
from app.services.attachment_runtime_loader import AttachmentRuntimeLoader
from app.services.context_assembler import ContextAssembler, ContextSelection
from app.services.model_service import ModelService
from app.services.platform_prompt_service import PlatformPromptService
from app.services.runtime_types import (
    CapabilityContext,
    CapabilityProvider,
    RuntimeObserver,
    RuntimeConversationTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from app.services.token_counter import RuntimeTokenCounter


_RESERVED_PROMPT_MODULES = frozenset(
    (*PlatformPromptService.BASE_MODULES, "attachments")
)


class RuntimeTerminalError(RuntimeError):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = message
        self.status_code = status_code


@dataclass(frozen=True)
class RuntimeTurn:
    context: CapabilityContext
    agent_prompt: str
    provider: str
    model: str
    turns: tuple[RuntimeConversationTurn, ...]


@dataclass(frozen=True)
class PreparedRuntimeTurn:
    context: CapabilityContext
    agent_prompt: str
    provider: str
    model: str
    turns: tuple[RuntimeConversationTurn, ...]
    provider_modules: tuple[str, ...]
    tool_definitions: tuple[ToolDefinition, ...]
    tool_owners: tuple[tuple[str, CapabilityProvider], ...]
    selection: ContextSelection


class AgentRuntime:
    def __init__(
        self,
        *,
        model_service: ModelService,
        prompt_service: PlatformPromptService,
        attachment_loader: AttachmentRuntimeLoader,
        context_assembler: ContextAssembler,
        agent_home: AgentHomeService,
        token_counter: RuntimeTokenCounter,
        tool_result_token_reserve: int,
        capability_providers: Sequence[CapabilityProvider] = (),
    ) -> None:
        if tool_result_token_reserve <= 0:
            raise ValueError("tool_result_token_reserve must be positive")
        if context_assembler.token_counter is not token_counter:
            raise ValueError("runtime and context assembler must share one token counter")
        self.model_service = model_service
        self.prompt_service = prompt_service
        self.attachment_loader = attachment_loader
        self.context_assembler = context_assembler
        self.agent_home = agent_home
        self.token_counter = token_counter
        self.tool_result_token_reserve = tool_result_token_reserve
        self.max_tool_rounds = DEFAULT_RUNTIME_MAX_TOOL_ROUNDS
        self.capability_providers = tuple(capability_providers)

    def configure_max_tool_rounds(self, max_tool_rounds: int) -> None:
        if type(max_tool_rounds) is not int or not 1 <= max_tool_rounds <= MAX_RUNTIME_MAX_TOOL_ROUNDS:
            raise ValueError(
                f"max_tool_rounds must be between 1 and {MAX_RUNTIME_MAX_TOOL_ROUNDS}"
            )
        self.max_tool_rounds = max_tool_rounds

    def prepare_turn(self, turn: RuntimeTurn) -> PreparedRuntimeTurn:
        definitions: list[ToolDefinition] = []
        owners: dict[str, CapabilityProvider] = {}
        for provider in self.capability_providers:
            for definition in provider.tool_definitions(turn.context):
                if definition.name in owners:
                    raise RuntimeError(
                        f"duplicate capability tool: {definition.name}"
                    )
                owners[definition.name] = provider
                definitions.append(definition)

        modules = tuple(
            module
            for provider in self.capability_providers
            for module in provider.prompt_modules(turn.context)
        )
        if _RESERVED_PROMPT_MODULES.intersection(modules):
            raise RuntimeError(
                "capability provider requested a reserved prompt module"
            )
        definition_tuple = tuple(definitions)
        selection = self._select_turns(
            context=turn.context,
            agent_prompt=turn.agent_prompt,
            turns=turn.turns,
            provider_modules=modules,
            definitions=definition_tuple,
            agents_md=None,
        )
        return PreparedRuntimeTurn(
            context=turn.context,
            agent_prompt=turn.agent_prompt,
            provider=turn.provider,
            model=turn.model,
            turns=turn.turns,
            provider_modules=modules,
            tool_definitions=definition_tuple,
            tool_owners=tuple(owners.items()),
            selection=selection,
        )

    def stream_turn(
        self,
        turn: PreparedRuntimeTurn,
        *,
        observer: RuntimeObserver,
    ) -> Iterator[str | ToolResult]:
        agents_md: str | None = None
        home = turn.context.agent_config.home_workspace
        if home is not None:
            try:
                root = self.agent_home.ensure_agents_md(
                    user_id=turn.context.user_id,
                    workspace_id=home.workspace_id,
                    initial_content=home.initial_agents_md,
                )
            except WorkspaceUnavailable as error:
                raise RuntimeTerminalError(
                    code="workspace_unavailable",
                    message="The workspace is temporarily unavailable.",
                    status_code=503,
                ) from error
            agents_md = root.content

        selection = self._select_turns(
            context=turn.context,
            agent_prompt=turn.agent_prompt,
            turns=turn.turns,
            provider_modules=turn.provider_modules,
            definitions=turn.tool_definitions,
            agents_md=agents_md,
        )
        attachments = {}
        for conversation_turn in selection.turns:
            for ref in conversation_turn.user.attachment_refs:
                attachments[ref.id] = self.attachment_loader.load(ref)
        messages = self.context_assembler.render_selected(
            selection,
            attachments=attachments,
        )

        owners = dict(turn.tool_owners)
        original_context = turn.context
        call_index = 1
        for _tool_round in range(self.max_tool_rounds):
            requested: ToolCall | None = None
            model_events = self.model_service.stream_turn(
                messages,
                provider=turn.provider,
                model=turn.model,
                call_index=call_index,
                observer=observer,
                tools=turn.tool_definitions or None,
            )
            call_index += 1
            try:
                for model_event in model_events:
                    if isinstance(model_event, ToolCall):
                        requested = model_event
                        break
                    if not isinstance(model_event, str):
                        raise TypeError("unsupported runtime model event")
                    yield model_event
            finally:
                close = getattr(model_events, "close", None)
                if callable(close):
                    close()
            if requested is None:
                return

            used_tokens = self.token_counter.count_model_input(
                messages,
                tools=turn.tool_definitions,
            ) + self.token_counter.count_assistant_tool_call(requested)
            remaining = original_context.token_budget - used_tokens
            if remaining <= 0:
                yield terminal_budget_audit(requested)
                raise _context_too_large_terminal()

            owner = owners.get(requested.name)
            if owner is None:
                candidate = ToolResult(
                    call_id=requested.id,
                    content=(
                        '{"code":"tool_not_found",'
                        '"message":"The requested tool is unavailable."}'
                    ),
                    is_error=True,
                    metadata={
                        "tool": requested.name,
                        "code": "tool_not_found",
                    },
                )
            else:
                candidate = owner.execute(
                    requested,
                    replace(original_context, token_budget=remaining),
                )

            if self.token_counter.count_tool_result(
                call_id=requested.id,
                content=candidate.content,
            ) > remaining:
                result = context_too_large_result(requested)
            else:
                result = candidate
            if self.token_counter.count_tool_result(
                call_id=requested.id,
                content=result.content,
            ) > remaining:
                yield terminal_budget_audit(requested)
                raise _context_too_large_terminal()

            yield result
            messages.extend(
                self.model_service.tool_result_messages(requested, result)
            )
        raise RuntimeError("tool_call_limit_exceeded")

    def _select_turns(
        self,
        *,
        context: CapabilityContext,
        agent_prompt: str,
        turns: tuple[RuntimeConversationTurn, ...],
        provider_modules: tuple[str, ...],
        definitions: tuple[ToolDefinition, ...],
        agents_md: str | None,
    ) -> ContextSelection:
        base_platform_prompt = self.prompt_service.build_platform_prompt(
            extra_modules=provider_modules,
        )
        attachment_platform_prompt = None
        if any(turn.user.attachment_refs for turn in turns):
            attachment_platform_prompt = self.prompt_service.build_platform_prompt(
                extra_modules=(*provider_modules, "attachments"),
            )
        return self.context_assembler.select_turns(
            history=turns,
            base_system_prompt=base_platform_prompt,
            attachment_system_prompt=attachment_platform_prompt,
            agent_prompt=agent_prompt,
            agents_md=agents_md,
            tool_definitions=definitions,
            token_budget=context.token_budget,
            tool_result_token_reserve=self.tool_result_token_reserve,
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
