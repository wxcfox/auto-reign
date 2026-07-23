from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeAlias

from sqlalchemy.orm import Session, sessionmaker

from app.services.agent_service import ResolvedAgentConfig


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


AssistantContent: TypeAlias = str | list[dict[str, object]] | None


@dataclass(frozen=True)
class AssistantMessageEvent:
    content: AssistantContent
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning_content: str | None = None
    provider: str | None = None
    model: str | None = None
    compacted: bool = False
    summary_compacted: bool = False
    compaction_version: int | None = None


@dataclass(frozen=True)
class ToolStartEvent:
    call: ToolCall


@dataclass(frozen=True)
class ToolResultEvent:
    call: ToolCall
    result: ToolResult


@dataclass(frozen=True)
class TextDeltaEvent:
    content: str


@dataclass(frozen=True)
class ProviderReasoningDelta:
    content: str


RuntimeEvent: TypeAlias = (
    AssistantMessageEvent | ToolStartEvent | ToolResultEvent | TextDeltaEvent
)


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


class RuntimeTerminalError(RuntimeError):
    def __init__(self, *, code: str, message: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = message
        self.status_code = status_code


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
class RuntimeTextContext:
    context_id: int
    source_type: Literal["attachment", "knowledge_base"]
    name: str
    text: str


@dataclass(frozen=True)
class RuntimeImageContext:
    context_id: int
    name: str
    mime_type: str
    image_base64: str


@dataclass(frozen=True)
class RuntimeSelectedDocumentsContext:
    context_id: int
    name: str
    knowledge_id: str
    document_ids: tuple[str, ...]


RuntimeUserContext: TypeAlias = (
    RuntimeTextContext | RuntimeImageContext | RuntimeSelectedDocumentsContext
)


@dataclass(frozen=True)
class RuntimeUserTurn:
    message_id: str
    text: str
    contexts: tuple[RuntimeUserContext, ...] = ()


@dataclass(frozen=True)
class RuntimeTaskTurn:
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
