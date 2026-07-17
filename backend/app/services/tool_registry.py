from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from app.services.runtime_types import (
    CapabilityContext,
    CapabilityProvider,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


@dataclass(frozen=True)
class ToolSpec:
    """One executable tool bound to its capability provider."""

    definition: ToolDefinition
    provider: CapabilityProvider

    def execute(self, call: ToolCall, context: CapabilityContext) -> ToolResult:
        return self.provider.execute(call, context)


@dataclass(frozen=True)
class ToolRegistrySnapshot:
    """The tools bound to one prepared runtime turn."""

    specs: Mapping[str, ToolSpec]
    prompt_modules: tuple[str, ...]

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(spec.definition for spec in self.specs.values())

    def get(self, name: str) -> ToolSpec | None:
        return self.specs.get(name)

    def execute(self, call: ToolCall, context: CapabilityContext) -> ToolResult:
        spec = self.get(call.name)
        if spec is None:
            return ToolResult(
                call_id=call.id,
                content=(
                    '{"code":"tool_not_found",'
                    '"message":"The requested tool is unavailable."}'
                ),
                is_error=True,
                metadata={"tool": call.name, "code": "tool_not_found"},
            )
        return spec.execute(call, context)


class ToolRegistry:
    """Binds capability providers to the tools available for one turn.

    Providers remain responsible for capability-specific authorization and
    execution. The registry only owns discovery, duplicate detection and
    dispatch, which keeps the ReAct loop independent from provider classes.
    """

    def __init__(self, providers: Sequence[CapabilityProvider] = ()) -> None:
        self._providers = tuple(providers)

    def bind(self, context: CapabilityContext) -> ToolRegistrySnapshot:
        specs: dict[str, ToolSpec] = {}
        modules: list[str] = []
        for provider in self._providers:
            for definition in provider.tool_definitions(context):
                if definition.name in specs:
                    raise RuntimeError(f"duplicate capability tool: {definition.name}")
                specs[definition.name] = ToolSpec(
                    definition=definition,
                    provider=provider,
                )
            modules.extend(provider.prompt_modules(context))
        return ToolRegistrySnapshot(
            specs=MappingProxyType(specs),
            prompt_modules=tuple(modules),
        )
