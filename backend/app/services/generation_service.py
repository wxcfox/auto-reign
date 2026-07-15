from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import logging
import math
import re
import time
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import bad_request, conflict, not_found
from app.core.limits import DEFAULT_RUNTIME_MAX_TOOL_ROUNDS, MAX_PROVIDER_TOKEN_COUNT
from app.core.request_context import get_request_id, is_safe_request_id
from app.db import models
from app.db.session import session_scope
from app.repositories.attachment_repository import AttachmentRepository
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.conversations import ConversationSendRequest
from app.schemas.modeling import ModelRef
from app.services.agent_runtime import (
    AgentRuntime,
    PreparedRuntimeTurn,
    RuntimeTerminalError,
    RuntimeTurn,
)
from app.services.agent_service import AgentService, ResolvedAgent
from app.services.attachment_runtime_loader import (
    AttachmentRuntimeError,
    RuntimeAttachmentRef,
)
from app.services.conversation_service import conversation_message_response
from app.services.runtime_types import (
    CapabilityContext,
    ProviderCallMetrics,
    RuntimeAssistantTurn,
    RuntimeConversationTurn,
    RuntimeObserver,
    RuntimeUserTurn,
    ToolResult,
)


logger = logging.getLogger(__name__)

_MAX_KNOWLEDGE_AUDIT_SOURCES = 20
_SAFE_PROVIDER_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_PROVIDER_UNAVAILABLE_FIELDS = (
    "provider_request_id",
    "input_tokens",
    "output_tokens",
    "first_token_latency_ms",
)


@dataclass(frozen=True)
class GenerationEvent:
    event: Literal["accepted", "delta", "result"]
    data: dict[str, object]


@dataclass(frozen=True)
class PreparedGeneration:
    conversation_id: str
    user_message_id: str | None
    assistant_message_id: str
    runtime_turn: PreparedRuntimeTurn
    attachment_ids: tuple[str, ...] = ()


class PreparedGenerationError(Exception):
    """A generation failed after its durable turn records were committed."""

    def __init__(self, prepared: PreparedGeneration, cause: Exception) -> None:
        super().__init__("generation failed after turn preparation")
        self.conversation_id = prepared.conversation_id
        self.assistant_message_id = prepared.assistant_message_id
        self.cause = cause


class ProviderMetricsInvalid(RuntimeError):
    """The runtime emitted provider audit data outside the sealed contract."""

    def __init__(self) -> None:
        super().__init__("provider metrics event is invalid")


class GenerationService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        runtime: AgentRuntime,
        agent_service: AgentService | None = None,
        repository: ConversationRepository | None = None,
        attachment_repository: AttachmentRepository | None = None,
        settings: Settings | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.runtime = runtime
        self.settings = settings or get_settings()
        self.agent_service = agent_service or AgentService(settings=self.settings)
        self.repository = repository or ConversationRepository()
        self.attachments = attachment_repository or AttachmentRepository()
        self.clock = clock or time.monotonic

    def stream_turn(
        self,
        *,
        user_id: int,
        request: ConversationSendRequest,
    ) -> Iterator[GenerationEvent]:
        prepared = self._prepare_turn(user_id=user_id, request=request)
        yield from self._stream_prepared(user_id=user_id, prepared=prepared)

    def stream_turn_for_api(
        self,
        *,
        user_id: int,
        request: ConversationSendRequest,
    ) -> Iterator[GenerationEvent]:
        prepared = self._prepare_turn(user_id=user_id, request=request)
        yield from self._stream_prepared_for_api(
            user_id=user_id,
            prepared=prepared,
        )

    def retry_turn(
        self,
        *,
        user_id: int,
        conversation_id: str,
        failed_message_id: str,
    ) -> Iterator[GenerationEvent]:
        prepared = self._prepare_retry(
            user_id=user_id,
            conversation_id=conversation_id,
            failed_message_id=failed_message_id,
        )
        yield from self._stream_prepared(user_id=user_id, prepared=prepared)

    def retry_turn_for_api(
        self,
        *,
        user_id: int,
        conversation_id: str,
        failed_message_id: str,
    ) -> Iterator[GenerationEvent]:
        prepared = self._prepare_retry(
            user_id=user_id,
            conversation_id=conversation_id,
            failed_message_id=failed_message_id,
        )
        yield from self._stream_prepared_for_api(
            user_id=user_id,
            prepared=prepared,
        )

    def recover_interrupted(self) -> int:
        with session_scope(self.session_factory) as session:
            return self.repository.recover_interrupted(session)

    def _prepare_turn(
        self,
        *,
        user_id: int,
        request: ConversationSendRequest,
    ) -> PreparedGeneration:
        text = request.text.strip()
        if not text:
            raise bad_request(
                "chat_message_empty",
                "Chat message text is required.",
            )

        with session_scope(self.session_factory) as session:
            if request.conversation_id is None:
                agent = (
                    self.agent_service.resolve_for_turn(
                        session,
                        user_id=user_id,
                        agent_id=request.agent_id,
                    )
                    if request.agent_id is not None
                    else None
                )
                model = self.agent_service.resolve_model(
                    agent=agent,
                    conversation_override=request.model_override,
                )
                conversation = self.repository.create_generating(
                    session,
                    user_id=user_id,
                    agent_id=agent.id if agent is not None else None,
                    title=_conversation_title(text),
                    model_override=request.model_override,
                )
            else:
                conversation = self.repository.get_for_update(
                    session,
                    user_id=user_id,
                    conversation_id=request.conversation_id,
                )
                if conversation is None:
                    raise not_found(
                        "conversation_not_found",
                        "Conversation not found.",
                    )
                if conversation.status != "idle":
                    raise conflict(
                        "generation_in_progress",
                        "This conversation already has an active generation.",
                    )
                if request.agent_id is not None and request.agent_id != conversation.agent_id:
                    raise bad_request(
                        "agent_locked",
                        "Agent cannot be changed in this conversation.",
                    )
                if request.model_override is not None:
                    raise bad_request(
                        "model_override_not_allowed",
                        "Use the conversation model setting before sending a message.",
                    )
                agent = (
                    self.agent_service.resolve_for_turn(
                        session,
                        user_id=user_id,
                        agent_id=conversation.agent_id,
                    )
                    if conversation.agent_id is not None
                    else None
                )
                model = self.agent_service.resolve_model(
                    agent=agent,
                    conversation_override=_conversation_model_override(conversation),
                )

            drafts = self.attachments.lock_drafts(
                session,
                user_id=user_id,
                attachment_ids=request.attachment_ids,
            )
            if len(drafts) != len(request.attachment_ids):
                raise conflict(
                    "attachment_not_ready",
                    "One or more attachments are unavailable.",
                )
            user_message, assistant = self.repository.append_pending_turn(
                session,
                conversation=conversation,
                text=text,
                provider=model.provider,
                model=model.model,
                metadata=self._generation_metadata(
                    agent or self.agent_service.plain_chat_agent()
                ),
            )
            self.attachments.bind_to_message(
                session,
                user_id=user_id,
                attachments=drafts,
                message_id=user_message.id,
            )
            turns = self._runtime_turns(
                session,
                user_id=user_id,
                conversation_id=conversation.id,
            )
            return self._prepared_generation(
                user_id=user_id,
                conversation_id=conversation.id,
                user_message_id=user_message.id,
                assistant_message_id=assistant.id,
                attachment_ids=tuple(request.attachment_ids),
                agent=agent or self.agent_service.plain_chat_agent(),
                provider=model.provider,
                model=model.model,
                turns=turns,
            )

    def _prepare_retry(
        self,
        *,
        user_id: int,
        conversation_id: str,
        failed_message_id: str,
    ) -> PreparedGeneration:
        with session_scope(self.session_factory) as session:
            conversation = self.repository.get_for_update(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if conversation is None:
                raise not_found(
                    "conversation_not_found",
                    "Conversation not found.",
                )
            if conversation.status != "idle":
                raise conflict(
                    "generation_in_progress",
                    "This conversation already has an active generation.",
                )
            failed = session.scalar(
                select(models.Message).where(
                    models.Message.id == failed_message_id,
                    models.Message.user_id == user_id,
                    models.Message.conversation_id == conversation_id,
                    models.Message.role == "assistant",
                    models.Message.status == "failed",
                )
            )
            if failed is None:
                raise not_found(
                    "message_not_found",
                    "Failed assistant message not found.",
                )

            agent = (
                self.agent_service.resolve_for_turn(
                    session,
                    user_id=user_id,
                    agent_id=conversation.agent_id,
                )
                if conversation.agent_id is not None
                else None
            )
            model = self.agent_service.resolve_model(
                agent=agent,
                conversation_override=_conversation_model_override(conversation),
            )
            next_sequence = (
                session.scalar(
                    select(func.max(models.Message.sequence)).where(
                        models.Message.user_id == user_id,
                        models.Message.conversation_id == conversation_id,
                    )
                )
                or 0
            ) + 1
            assistant = models.Message(
                user_id=user_id,
                conversation_id=conversation_id,
                sequence=next_sequence,
                role="assistant",
                status="pending",
                content="",
                provider=model.provider,
                model=model.model,
                metadata_json={
                    "retry_of_message_id": failed_message_id,
                    **self._generation_metadata(
                        agent or self.agent_service.plain_chat_agent()
                    ),
                },
            )
            conversation.status = "generating"
            conversation.updated_at = models._now()
            session.add(assistant)
            session.flush()
            turns = self._runtime_turns(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            return self._prepared_generation(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message_id=None,
                assistant_message_id=assistant.id,
                attachment_ids=(),
                agent=agent or self.agent_service.plain_chat_agent(),
                provider=model.provider,
                model=model.model,
                turns=turns,
            )

    def _stream_prepared(
        self,
        *,
        user_id: int,
        prepared: PreparedGeneration,
    ) -> Iterator[GenerationEvent]:
        buffer = ""
        completed = False
        runtime_stream: Iterator[str | ToolResult] | None = None
        provider_calls: list[dict[str, object]] = []
        provider_metrics_error: ProviderMetricsInvalid | None = None

        def observe_provider_call(metrics: ProviderCallMetrics) -> None:
            nonlocal provider_metrics_error
            if provider_metrics_error is not None:
                return
            try:
                projected = _project_provider_call(
                    metrics,
                    expected_call_index=len(provider_calls) + 1,
                    max_provider_calls=self.settings.runtime_max_tool_rounds,
                )
                _validate_provider_token_totals((*provider_calls, projected))
            except ProviderMetricsInvalid as error:
                provider_metrics_error = error
                return
            provider_calls.append(projected)

        observer: RuntimeObserver = observe_provider_call
        audit_started = self._read_clock()
        try:
            yield GenerationEvent(
                event="accepted",
                data={
                    "conversation_id": prepared.conversation_id,
                    "user_message_id": prepared.user_message_id,
                    "assistant_message_id": prepared.assistant_message_id,
                    "attachment_ids": list(prepared.attachment_ids),
                },
            )
            last_checkpoint = audit_started
            runtime_stream = self.runtime.stream_turn(
                prepared.runtime_turn,
                observer=observer,
            )
            streaming = False
            try:
                for runtime_event in runtime_stream:
                    if provider_metrics_error is not None:
                        raise provider_metrics_error
                    if isinstance(runtime_event, ToolResult):
                        self._append_tool_audit(
                            prepared.assistant_message_id,
                            user_id=user_id,
                            audit=_safe_tool_audit(runtime_event),
                        )
                        continue
                    if not isinstance(runtime_event, str):
                        raise TypeError("unsupported runtime event")
                    buffer += runtime_event
                    if not streaming:
                        self._mark_streaming(
                            prepared.assistant_message_id,
                            user_id=user_id,
                        )
                        streaming = True
                    now = self._read_clock()
                    if _at_least_one_second(now, last_checkpoint):
                        self._checkpoint(
                            prepared.assistant_message_id,
                            user_id=user_id,
                            content=buffer,
                        )
                        last_checkpoint = now
                    yield GenerationEvent(event="delta", data={"text": runtime_event})
            finally:
                self._close_runtime_stream(runtime_stream)
                runtime_stream = None

            if provider_metrics_error is not None:
                raise provider_metrics_error

            if not buffer.strip():
                raise RuntimeError("empty model response")
            audit_metadata = self._provider_audit_metadata(
                prepared=prepared,
                provider_calls=provider_calls,
                started=audit_started,
            )
            message = self._complete(
                prepared.assistant_message_id,
                user_id=user_id,
                content=buffer,
                audit_metadata=audit_metadata,
            )
            completed = True
            yield GenerationEvent(
                event="result",
                data={
                    "conversation_id": prepared.conversation_id,
                    "message": conversation_message_response(message).model_dump(mode="json"),
                },
            )
        except GeneratorExit:
            self._close_runtime_stream(runtime_stream)
            runtime_stream = None
            if not completed:
                error_code = (
                    _error_code(provider_metrics_error)
                    if provider_metrics_error is not None
                    else "generation_cancelled"
                )
                self._best_effort_fail(
                    prepared.assistant_message_id,
                    user_id=user_id,
                    content=buffer,
                    error_code=error_code,
                    audit_metadata=self._provider_audit_metadata(
                        prepared=prepared,
                        provider_calls=provider_calls,
                        started=audit_started,
                    ),
                )
            raise
        except Exception as error:
            self._close_runtime_stream(runtime_stream)
            runtime_stream = None
            terminal_error = provider_metrics_error or error
            if not completed:
                self._best_effort_fail(
                    prepared.assistant_message_id,
                    user_id=user_id,
                    content=buffer,
                    error_code=_error_code(terminal_error),
                    audit_metadata=self._provider_audit_metadata(
                        prepared=prepared,
                        provider_calls=provider_calls,
                        started=audit_started,
                    ),
                )
            if provider_metrics_error is not None and error is not provider_metrics_error:
                raise provider_metrics_error from error
            raise

    def _stream_prepared_for_api(
        self,
        *,
        user_id: int,
        prepared: PreparedGeneration,
    ) -> Iterator[GenerationEvent]:
        try:
            yield from self._stream_prepared(
                user_id=user_id,
                prepared=prepared,
            )
        except Exception as error:
            raise PreparedGenerationError(prepared, error) from error

    def _mark_streaming(self, message_id: str, *, user_id: int) -> None:
        with session_scope(self.session_factory) as session:
            self.repository.checkpoint_assistant(
                session,
                user_id=user_id,
                message_id=message_id,
                content="",
                status="streaming",
            )

    def _checkpoint(
        self,
        message_id: str,
        *,
        user_id: int,
        content: str,
    ) -> None:
        with session_scope(self.session_factory) as session:
            self.repository.checkpoint_assistant(
                session,
                user_id=user_id,
                message_id=message_id,
                content=content,
            )

    def _append_tool_audit(
        self,
        message_id: str,
        *,
        user_id: int,
        audit: dict[str, object],
    ) -> None:
        with session_scope(self.session_factory) as session:
            message = session.scalar(
                select(models.Message)
                .where(
                    models.Message.id == message_id,
                    models.Message.user_id == user_id,
                    models.Message.role == "assistant",
                )
                .with_for_update()
            )
            if message is None:
                raise ValueError("assistant_message_not_found")
            if message.status not in {"pending", "streaming"}:
                raise ValueError("assistant_message_not_writable")
            metadata = dict(message.metadata_json or {})
            existing = metadata.get("tool_calls")
            tool_calls = list(existing) if isinstance(existing, list) else []
            metadata["tool_calls"] = [*tool_calls, dict(audit)]
            message.metadata_json = metadata
            message.updated_at = models._now()
            session.flush()

    def _complete(
        self,
        message_id: str,
        *,
        user_id: int,
        content: str,
        audit_metadata: dict[str, object],
    ) -> models.Message:
        with session_scope(self.session_factory) as session:
            message = self.repository.finish_assistant(
                session,
                user_id=user_id,
                message_id=message_id,
                content=content,
                status="completed",
            )
            self._merge_audit_metadata(message, audit_metadata)
            session.flush()
            return message

    def _fail(
        self,
        message_id: str,
        *,
        user_id: int,
        content: str,
        error_code: str,
        audit_metadata: dict[str, object],
    ) -> None:
        with session_scope(self.session_factory) as session:
            message = self.repository.finish_assistant(
                session,
                user_id=user_id,
                message_id=message_id,
                content=content,
                status="failed",
                error_code=error_code,
            )
            self._merge_audit_metadata(message, audit_metadata)
            session.flush()

    def _best_effort_fail(
        self,
        message_id: str,
        *,
        user_id: int,
        content: str,
        error_code: str,
        audit_metadata: dict[str, object],
    ) -> None:
        try:
            self._fail(
                message_id,
                user_id=user_id,
                content=content,
                error_code=error_code,
                audit_metadata=audit_metadata,
            )
        except Exception as persistence_error:
            logger.error(
                "generation_failure_persistence_failed",
                extra={
                    "exception_type": type(persistence_error).__name__,
                    "error_code": "generation_failure_persistence_failed",
                },
                exc_info=False,
            )

    @staticmethod
    def _merge_audit_metadata(
        message: models.Message,
        audit_metadata: dict[str, object],
    ) -> None:
        message.metadata_json = {
            **(message.metadata_json or {}),
            **audit_metadata,
        }
        message.updated_at = models._now()

    def _provider_audit_metadata(
        self,
        *,
        prepared: PreparedGeneration,
        provider_calls: list[dict[str, object]],
        started: object,
    ) -> dict[str, object]:
        input_tokens = sum(
            value
            for call in provider_calls
            if type(value := call["input_tokens"]) is int
        )
        output_tokens = sum(
            value
            for call in provider_calls
            if type(value := call["output_tokens"]) is int
        )
        first_token_latency_ms = next(
            (
                value
                for call in provider_calls
                if (value := call["first_token_latency_ms"]) is not None
            ),
            None,
        )
        provider_request_id = next(
            (
                value
                for call in provider_calls
                if (value := call["provider_request_id"]) is not None
            ),
            None,
        )
        return {
            "conversation_id": prepared.conversation_id,
            "message_id": prepared.assistant_message_id,
            "provider_request_id": provider_request_id,
            "provider_calls": [dict(call) for call in provider_calls],
            "token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "incomplete": not provider_calls
                or any(
                    call["input_tokens"] is None
                    or call["output_tokens"] is None
                    for call in provider_calls
                ),
            },
            "first_token_latency_ms": first_token_latency_ms,
            "total_duration_ms": self._elapsed_milliseconds(started),
        }

    def _read_clock(self) -> object:
        try:
            return self.clock()
        except Exception:
            return 0.0

    def _elapsed_milliseconds(self, started: object) -> float:
        ended = self._read_clock()
        try:
            seconds = ended - started  # type: ignore[operator]
        except (OverflowError, TypeError, ValueError):
            return 0.0
        return _safe_milliseconds(seconds)

    @staticmethod
    def _close_runtime_stream(
        runtime_stream: Iterator[str | ToolResult] | None,
    ) -> None:
        close = getattr(runtime_stream, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as error:
            logger.error(
                "generation_runtime_close_failed",
                extra={
                    "exception_type": type(error).__name__,
                    "error_code": "generation_runtime_close_failed",
                },
                exc_info=False,
            )

    def _runtime_turns(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
    ) -> tuple[RuntimeConversationTurn, ...]:
        messages = self.repository.list_model_history(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            max_messages=200,
        )
        attachments = self.attachments.list_for_messages(
            session,
            user_id=user_id,
            message_ids=[message.id for message in messages],
        )
        attachments_by_message: dict[str, list[models.Attachment]] = {}
        for attachment in attachments:
            if attachment.message_id is not None:
                attachments_by_message.setdefault(attachment.message_id, []).append(
                    attachment
                )

        turns: list[RuntimeConversationTurn] = []
        for message in messages:
            if message.role == "user":
                refs = tuple(
                    _runtime_attachment_ref(attachment)
                    for attachment in attachments_by_message.get(message.id, ())
                )
                turns.append(
                    RuntimeConversationTurn(
                        user=RuntimeUserTurn(
                            message_id=message.id,
                            text=message.content,
                            attachment_refs=refs,
                        )
                    )
                )
            elif message.role == "assistant" and turns:
                previous = turns[-1]
                turns[-1] = RuntimeConversationTurn(
                    user=previous.user,
                    assistants=(
                        *previous.assistants,
                        RuntimeAssistantTurn(
                            message_id=message.id,
                            text=message.content,
                        ),
                    ),
                )
        return tuple(turns)

    def _prepared_generation(
        self,
        *,
        user_id: int,
        conversation_id: str,
        user_message_id: str | None,
        assistant_message_id: str,
        attachment_ids: tuple[str, ...],
        agent: ResolvedAgent,
        provider: str,
        model: str,
        turns: tuple[RuntimeConversationTurn, ...],
    ) -> PreparedGeneration:
        context = CapabilityContext(
            user_id=user_id,
            agent_config=agent.config,
            session_factory=self.session_factory,
            token_budget=self.settings.chat_context_token_budget,
        )
        runtime_turn = self.runtime.prepare_turn(
            RuntimeTurn(
                context=context,
                agent_prompt=agent.config.system_prompt,
                provider=provider,
                model=model,
                turns=turns,
            )
        )
        return PreparedGeneration(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            runtime_turn=runtime_turn,
            attachment_ids=attachment_ids,
        )

    def _generation_metadata(self, agent: ResolvedAgent) -> dict[str, object]:
        metadata: dict[str, object] = {
            "agent_config_updated_at": agent.updated_at.isoformat(),
            "agent_config_hash": agent.config_hash,
            "platform_prompt_version": self.settings.app_version,
        }
        request_id = get_request_id()
        if is_safe_request_id(request_id):
            metadata["request_id"] = request_id
        return metadata


def _project_provider_call(
    metrics: ProviderCallMetrics,
    *,
    expected_call_index: int,
    max_provider_calls: int = DEFAULT_RUNTIME_MAX_TOOL_ROUNDS,
) -> dict[str, object]:
    if type(metrics) is not ProviderCallMetrics:
        raise ProviderMetricsInvalid()
    if (
        type(metrics.call_index) is not int
        or metrics.call_index != expected_call_index
        or metrics.call_index < 1
        or metrics.call_index > max_provider_calls
    ):
        raise ProviderMetricsInvalid()
    provider = _safe_audit_text(metrics.provider, max_bytes=256)
    model = _safe_audit_text(metrics.model, max_bytes=256)
    if provider is None or model is None:
        raise ProviderMetricsInvalid()
    provider_request_id = metrics.provider_request_id
    if provider_request_id is not None and (
        not isinstance(provider_request_id, str)
        or _SAFE_PROVIDER_REQUEST_ID.fullmatch(provider_request_id) is None
    ):
        raise ProviderMetricsInvalid()
    input_tokens = _validated_token_count(metrics.input_tokens)
    output_tokens = _validated_token_count(metrics.output_tokens)
    first_token_latency_ms = _validated_optional_milliseconds(
        metrics.first_token_latency_ms
    )
    duration_ms = _validated_milliseconds(metrics.duration_ms)
    if not isinstance(metrics.status, str) or metrics.status not in (
        "completed",
        "failed",
    ):
        raise ProviderMetricsInvalid()
    expected_unavailable = tuple(
        field
        for field, value in (
            ("provider_request_id", provider_request_id),
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
            ("first_token_latency_ms", first_token_latency_ms),
        )
        if value is None
    )
    if (
        type(metrics.unavailable_fields) is not tuple
        or metrics.unavailable_fields != expected_unavailable
        or any(field not in _PROVIDER_UNAVAILABLE_FIELDS for field in expected_unavailable)
    ):
        raise ProviderMetricsInvalid()
    return {
        "call_index": metrics.call_index,
        "provider": provider,
        "model": model,
        "provider_request_id": provider_request_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "first_token_latency_ms": first_token_latency_ms,
        "duration_ms": duration_ms,
        "status": metrics.status,
        "unavailable_fields": list(expected_unavailable),
    }


def _validated_token_count(value: object) -> int | None:
    if value is None:
        return None
    if (
        type(value) is not int
        or value < 0
        or value > MAX_PROVIDER_TOKEN_COUNT
    ):
        raise ProviderMetricsInvalid()
    return value


def _validated_optional_milliseconds(value: object) -> float | None:
    if value is None:
        return None
    return _validated_milliseconds(value)


def _validated_milliseconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderMetricsInvalid()
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ProviderMetricsInvalid() from error
    if not math.isfinite(converted) or converted < 0:
        raise ProviderMetricsInvalid()
    return converted


def _validate_provider_token_totals(
    provider_calls: tuple[dict[str, object], ...],
) -> None:
    for field in ("input_tokens", "output_tokens"):
        total = 0
        for call in provider_calls:
            value = call[field]
            if type(value) is int:
                total += value
        if total > MAX_PROVIDER_TOKEN_COUNT:
            raise ProviderMetricsInvalid()


def _at_least_one_second(ended: object, started: object) -> bool:
    try:
        seconds = ended - started  # type: ignore[operator]
        converted = float(seconds)
    except (OverflowError, TypeError, ValueError):
        return False
    return math.isfinite(converted) and converted >= 1.0


def _safe_milliseconds(seconds: object) -> float:
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


def _conversation_model_override(
    conversation: models.Conversation,
) -> ModelRef | None:
    if conversation.model_override_json is None:
        return None
    return ModelRef.model_validate(conversation.model_override_json)


def _conversation_title(text: str) -> str:
    return " ".join(text.split())[:80]


def _error_code(error: Exception) -> str:
    if isinstance(error, ProviderMetricsInvalid):
        return "provider_metrics_invalid"
    if isinstance(error, RuntimeTerminalError):
        return error.code
    if isinstance(error, AttachmentRuntimeError):
        if error.code in {"attachment_unavailable", "attachment_corrupt"}:
            return error.code
        return "attachment_unavailable"
    if isinstance(error, HTTPException) and isinstance(error.detail, dict):
        code = error.detail.get("code")
        if isinstance(code, str) and code:
            return code
    return "provider_call_failed"


def _safe_tool_audit(result: ToolResult) -> dict[str, object]:
    metadata = result.metadata
    audit: dict[str, object] = {
        "call_id": _safe_audit_text(result.call_id, max_bytes=256) or "invalid",
        "tool": _safe_audit_text(metadata.get("tool"), max_bytes=128) or "unknown",
        "status": "error" if result.is_error else "completed",
    }
    if result.is_error:
        code = _safe_audit_text(metadata.get("code"), max_bytes=80)
        if code is not None:
            audit["code"] = code
        if metadata.get("terminal") is True:
            audit["terminal"] = True
        return audit

    if audit["tool"] == "search_knowledge":
        mode = metadata.get("mode")
        sources = metadata.get("sources")
        if mode in {"direct", "rag"} and isinstance(sources, list):
            audit["mode"] = mode
            audit["sources"] = [
                projected
                for source in sources[:_MAX_KNOWLEDGE_AUDIT_SOURCES]
                if (projected := _safe_knowledge_source_audit(source)) is not None
            ]
        return audit

    path_digest = metadata.get("path_sha256")
    if (
        isinstance(path_digest, str)
        and len(path_digest) == 64
        and all(character in "0123456789abcdef" for character in path_digest)
    ):
        audit["path_sha256"] = path_digest
    etag = _safe_audit_text(metadata.get("etag"), max_bytes=256)
    if etag is not None:
        audit["etag"] = etag
    return audit


def _safe_knowledge_source_audit(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    document_id = _safe_audit_text(value.get("document_id"), max_bytes=256)
    collection_id = _safe_audit_text(value.get("collection_id"), max_bytes=256)
    filename = _safe_audit_text(value.get("filename"), max_bytes=1_024)
    content_hash = _safe_audit_text(value.get("content_hash"), max_bytes=256)
    index_generation = value.get("index_generation")
    chunk_index = value.get("chunk_index")
    score = value.get("score")
    if (
        document_id is None
        or collection_id is None
        or filename is None
        or content_hash is None
        or type(index_generation) is not int
        or index_generation < 1
        or (
            chunk_index is not None
            and (type(chunk_index) is not int or chunk_index < 0)
        )
        or (
            score is not None
            and (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
            )
        )
    ):
        return None
    return {
        "document_id": document_id,
        "collection_id": collection_id,
        "filename": filename,
        "index_generation": index_generation,
        "content_hash": content_hash,
        "chunk_index": chunk_index,
        "score": float(score) if score is not None else None,
    }


def _safe_audit_text(value: object, *, max_bytes: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    if len(encoded) > max_bytes:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value


def _runtime_attachment_ref(
    attachment: models.Attachment,
) -> RuntimeAttachmentRef:
    return RuntimeAttachmentRef(
        id=attachment.id,
        filename=attachment.original_filename,
        media_type=attachment.mime_type,
        source_object_key=attachment.object_key,
        parsed_object_key=attachment.parsed_object_key,
        source_size_bytes=attachment.size_bytes,
        source_content_hash=attachment.content_hash,
        parsed_size_bytes=attachment.parsed_size_bytes,
        parsed_content_hash=attachment.parsed_content_hash,
    )
