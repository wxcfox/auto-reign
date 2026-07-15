from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.services.agent_service import ResolvedAgentConfig
from app.services.attachment_runtime_loader import RuntimeAttachmentRef


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCallMetrics:
    call_index: int
    provider: str
    model: str
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    first_token_latency_ms: float | None
    duration_ms: float
    status: Literal["completed", "failed"]
    unavailable_fields: tuple[str, ...]


RuntimeObserver = Callable[[ProviderCallMetrics], None]


@dataclass(frozen=True)
class CapabilityContext:
    user_id: int
    agent_config: ResolvedAgentConfig
    session_factory: sessionmaker[Session]
    token_budget: int


@dataclass(frozen=True)
class RuntimeAssistantTurn:
    message_id: str
    text: str


@dataclass(frozen=True)
class RuntimeUserTurn:
    message_id: str
    text: str
    attachment_refs: tuple[RuntimeAttachmentRef, ...] = ()


@dataclass(frozen=True)
class RuntimeConversationTurn:
    user: RuntimeUserTurn
    assistants: tuple[RuntimeAssistantTurn, ...] = ()


class CapabilityProvider(Protocol):
    def prompt_modules(self, context: CapabilityContext) -> tuple[str, ...]:
        pass

    def tool_definitions(
        self,
        context: CapabilityContext,
    ) -> tuple[ToolDefinition, ...]:
        pass

    def execute(self, call: ToolCall, context: CapabilityContext) -> ToolResult:
        pass
