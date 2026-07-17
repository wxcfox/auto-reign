from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from app.services.agent_home_service import AgentHomeService, WorkspaceUnavailable
from app.services.attachment_runtime_loader import AttachmentRuntimeLoader
from app.services.context_assembler import ContextAssembler, ContextSelection
from app.services.model_service import ModelService
from app.services.platform_prompt_service import PlatformPromptService
from app.services.react_loop import ReactLoop
from app.services.runtime_types import (
    CapabilityContext,
    CapabilityProvider,
    RuntimeObserver,
    RuntimeTerminalError,
    RuntimeConversationTurn,
    ToolDefinition,
    ToolResult,
)
from app.services.token_counter import RuntimeTokenCounter
from app.services.tool_registry import ToolRegistry, ToolRegistrySnapshot


_RESERVED_PROMPT_MODULES = frozenset(
    (*PlatformPromptService.BASE_MODULES, "attachments")
)


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
    tool_registry: ToolRegistrySnapshot


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
        self.prompt_service = prompt_service
        self.attachment_loader = attachment_loader
        self.context_assembler = context_assembler
        self.agent_home = agent_home
        self.tool_result_token_reserve = tool_result_token_reserve
        self.react_loop = ReactLoop(
            model_service=model_service,
            token_counter=token_counter,
        )
        self._tool_registry = ToolRegistry(capability_providers)

    def configure_max_tool_rounds(self, max_tool_rounds: int) -> None:
        self.react_loop.configure_max_tool_rounds(max_tool_rounds)

    def prepare_turn(self, turn: RuntimeTurn) -> PreparedRuntimeTurn:
        registry = self._tool_registry.bind(turn.context)
        definitions = registry.definitions
        modules = registry.prompt_modules
        if _RESERVED_PROMPT_MODULES.intersection(modules):
            raise RuntimeError(
                "capability provider requested a reserved prompt module"
            )
        self._select_turns(
            context=turn.context,
            agent_prompt=turn.agent_prompt,
            turns=turn.turns,
            provider_modules=modules,
            definitions=definitions,
            agents_md=None,
        )
        return PreparedRuntimeTurn(
            context=turn.context,
            agent_prompt=turn.agent_prompt,
            provider=turn.provider,
            model=turn.model,
            turns=turn.turns,
            tool_registry=registry,
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
            provider_modules=turn.tool_registry.prompt_modules,
            definitions=turn.tool_registry.definitions,
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

        yield from self.react_loop.stream(
            messages,
            provider=turn.provider,
            model=turn.model,
            context=turn.context,
            registry=turn.tool_registry,
            observer=observer,
        )

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
