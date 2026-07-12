from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.errors import bad_gateway, conflict, not_found
from app.core.model_providers import default_chat_provider, preferred_chat_provider
from app.db import models
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.interviews import InterviewConfigIn
from app.schemas.modeling import (
    AnswerEvaluationRequest,
    AnswerEvaluationResult,
    QuestionGenerationRequest,
    ReportGenerationRequest,
)
from app.services.artifact_service import ArtifactService
from app.services.context_assembler import ContextAssembler
from app.services.content_renderer import render_answer_preview, render_interview_report
from app.services.interview_artifact_service import InterviewArtifactService
from app.services.model_service import ModelService
from app.services.retrieval_query_planner import RetrievalPurpose, RetrievalRequest
from app.services.workspace_paths import (
    CANDIDATE_PROFILE_PATH,
    HIGH_FREQUENCY_PATH,
    MANIFEST_PATH,
    MASTERY_PATH,
    REVIEW_STATUS_PATH,
    TARGET_PROFILE_PATH,
)
from app.services.workspace_retrieval_service import WorkspaceRetrievalService
from app.services.workspace_service import WorkspaceService


DIRECT_WORKSPACE_CONTEXT_FILES = (
    (MANIFEST_PATH, "工作区清单"),
    (CANDIDATE_PROFILE_PATH, "候选人画像"),
    (TARGET_PROFILE_PATH, "目标画像"),
    (MASTERY_PATH, "掌握状态"),
    (REVIEW_STATUS_PATH, "复习状态"),
    (HIGH_FREQUENCY_PATH, "高频与薄弱点"),
)
PROJECT_CONTEXT_LIMIT = 3


@dataclass
class InterviewStreamEvent:
    event: str
    data: dict[str, Any]


@dataclass
class InterviewConfigDTO:
    id: str
    target_company: str
    target_role: str
    job_description: str
    extra_prompt: str
    language: str
    mode: str
    chat_model_provider: str
    chat_model: str
    target_rounds: int
    is_last_used: bool
    updated_at: datetime


@dataclass
class InterviewSessionDTO:
    id: str
    config_id: str
    status: str
    current_round: int
    started_at: datetime
    ended_at: datetime | None = None
    report_path: str | None = None
    config: InterviewConfigDTO | None = None
    turns: list["InterviewTurnDTO"] = field(default_factory=list)


@dataclass
class InterviewTurnDTO:
    id: str
    session_id: str
    round_index: int
    question: str
    answer: str | None = None
    feedback: str | None = None
    missing_points: list[str] = field(default_factory=list)
    follow_up_question: str | None = None
    follow_up_answer: str | None = None
    follow_up_feedback: str | None = None
    follow_up_missing_points: list[str] = field(default_factory=list)
    follow_up_weaknesses: list[str] = field(default_factory=list)
    follow_up_review_suggestions: list[str] = field(default_factory=list)
    follow_up_better_answer: str = ""
    follow_up_mastery_change: str = "unchanged"
    follow_up_should_write_weakness: bool = False
    follow_up_should_write_high_frequency: bool = False
    follow_up_tested_points: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    review_suggestions: list[str] = field(default_factory=list)
    better_answer: str = ""
    mastery_change: str = "unchanged"
    should_write_weakness: bool = False
    should_write_high_frequency: bool = False
    tested_points: list[str] = field(default_factory=list)
    retrieved_context_refs: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ReportDTO:
    id: str
    session_id: str
    report_path: str
    summary: str
    weaknesses: list[str]
    created_at: datetime


def _target_context_query(
    *,
    target_company: str,
    target_role: str,
    job_description: str,
    extra_prompt: str,
    round_index: int | None = None,
) -> str:
    parts = [
        item.strip()
        for item in [target_company, target_role, job_description, extra_prompt]
        if item.strip()
    ]
    if round_index is not None:
        parts.append(f"round {round_index}")
    return " ".join(parts)


def _answer_context_query(
    *,
    target_company: str,
    target_role: str,
    job_description: str,
    extra_prompt: str,
    question: str,
    answer: str,
) -> str:
    parts = [
        _target_context_query(
            target_company=target_company,
            target_role=target_role,
            job_description=job_description,
            extra_prompt=extra_prompt,
        ),
        question,
        answer,
    ]
    return " ".join(item.strip() for item in parts if item.strip())


class InterviewService:
    def __init__(
        self,
        *,
        user_id: int,
        conversation_repository: ConversationRepository | None = None,
        model_service: ModelService | None = None,
        retrieval_service: WorkspaceRetrievalService | None = None,
        context_assembler: ContextAssembler | None = None,
        artifact_repository: ArtifactRepository | None = None,
        artifact_service: ArtifactService | None = None,
        workspace_service: WorkspaceService | None = None,
        interview_artifact_service: InterviewArtifactService | None = None,
    ) -> None:
        self.user_id = user_id
        self.conversations = conversation_repository or ConversationRepository()
        self.model_service = model_service or ModelService()
        self.settings = self.model_service.settings
        self.retrieval_service = retrieval_service or WorkspaceRetrievalService(user_id=user_id)
        self.context_assembler = context_assembler or ContextAssembler()
        self.artifact_repository = artifact_repository or ArtifactRepository()
        self.artifact_service = artifact_service
        self.workspace_service = workspace_service
        self.interview_artifact_service = interview_artifact_service

    def get_last_config(self, session: Session) -> InterviewConfigDTO:
        user = session.get(models.User, self.user_id)
        settings = dict(user.settings_json or {}) if user is not None else {}
        saved = settings.get("last_interview_config")
        if isinstance(saved, dict):
            return self._config_from_dict(saved, is_last_used=True)
        return self.save_last_config(session, self._default_config())

    def save_last_config(
        self, session: Session, config_in: InterviewConfigIn
    ) -> InterviewConfigDTO:
        user = session.get(models.User, self.user_id)
        now = datetime.now(UTC)
        payload = self._config_payload(config_in, config_id=str(uuid4()), updated_at=now)
        if user is not None:
            user.settings_json = {
                **(user.settings_json or {}),
                "last_interview_config": payload,
            }
            session.flush()
        return self._config_from_dict(payload, is_last_used=True)

    def stream_create_session(
        self, session: Session, config_in: InterviewConfigIn
    ) -> Iterator[InterviewStreamEvent]:
        config_in = self._config_from_natural_intent(config_in)
        context, context_hits = self._question_context(
            session,
            _target_context_query(
                target_company=config_in.target_company,
                target_role=config_in.target_role,
                job_description=config_in.job_description,
                extra_prompt=config_in.extra_prompt,
            ),
            mode=config_in.mode,
        )
        request = self._question_request(config_in, context)

        def events() -> Iterator[InterviewStreamEvent]:
            question = yield from self._stream_question_events(request)
            conversation = self.conversations.create(
                session,
                user_id=self.user_id,
                kind="interview",
                title=self._conversation_title(config_in),
                status="active",
                config_json=self._config_payload(config_in, config_id=str(uuid4())),
                summary_json={"current_round": 1, "last_message": question},
            )
            self._add_question_message(session, conversation.id, 1, question, context_hits)
            session.flush()
            interview_session, turn = self._session_and_current_turn(session, conversation)
            yield InterviewStreamEvent(
                event="result",
                data={"session": interview_session, "turn": turn},
            )

        return events()

    def get_session_detail(
        self, session: Session, session_id: str
    ) -> tuple[InterviewSessionDTO, InterviewConfigDTO, list[InterviewTurnDTO]] | None:
        conversation = self.conversations.get(
            session,
            user_id=self.user_id,
            conversation_id=session_id,
            kind="interview",
        )
        if conversation is None:
            return None
        config = self._conversation_config(conversation)
        turns = self._turns_for_conversation(session, conversation)
        interview_session = self._session_response(conversation, config, turns)
        return interview_session, config, turns

    def stream_submit_answer(
        self, session: Session, session_id: str, answer: str
    ) -> Iterator[InterviewStreamEvent]:
        conversation = self._get_active_conversation(session, session_id)
        config = self._conversation_config(conversation)
        turn = self._current_turn(session, conversation)
        if turn.answer is not None:
            raise conflict("answer_already_submitted", "The current answer was already submitted.")
        request = self._answer_request(session, config, question=turn.question, answer=answer)

        def events() -> Iterator[InterviewStreamEvent]:
            evaluation = yield from self._stream_answer_evaluation_events(request)
            self._store_main_evaluation(session, conversation, turn, answer, evaluation)
            self._persist_practice_progress(session, conversation)
            yield InterviewStreamEvent(event="result", data=evaluation.model_dump())

        return events()

    def stream_submit_follow_up_answer(
        self, session: Session, session_id: str, answer: str
    ) -> Iterator[InterviewStreamEvent]:
        conversation = self._get_active_conversation(session, session_id)
        config = self._conversation_config(conversation)
        turn = self._current_turn(session, conversation)
        if turn.answer is None or not turn.follow_up_question:
            raise conflict("main_answer_required", "Submit the main answer before the follow-up.")
        if turn.follow_up_answer is not None:
            raise conflict(
                "follow_up_already_submitted",
                "The current follow-up answer was already submitted.",
            )
        request = self._answer_request(
            session,
            config,
            question=turn.follow_up_question,
            answer=answer,
            retrieval_purpose="follow_up_feedback",
        )

        def events() -> Iterator[InterviewStreamEvent]:
            evaluation = yield from self._stream_answer_evaluation_events(request)
            self._store_follow_up_evaluation(session, conversation, turn, answer, evaluation)
            self._persist_practice_progress(session, conversation)
            yield InterviewStreamEvent(event="result", data=evaluation.model_dump())

        return events()

    def stream_next_question(
        self, session: Session, session_id: str, *, intent: str = ""
    ) -> Iterator[InterviewStreamEvent]:
        conversation = self._get_active_conversation(session, session_id)
        config = self._conversation_config(conversation)
        current_turn = self._current_turn(session, conversation)
        if current_turn.answer is None:
            raise conflict("current_turn_unanswered", "Answer the current question before continuing.")
        next_round = current_turn.round_index + 1
        next_extra_prompt = self._apply_next_intent(conversation, config, intent)
        context, context_hits = self._question_context(
            session,
            _target_context_query(
                target_company=config.target_company,
                target_role=config.target_role,
                job_description=config.job_description,
                extra_prompt=next_extra_prompt,
                round_index=next_round,
            ),
            mode=config.mode,
        )
        request = self._question_request(config, context, extra_prompt=next_extra_prompt)

        def events() -> Iterator[InterviewStreamEvent]:
            question = yield from self._stream_question_events(request)
            self._add_question_message(session, conversation.id, next_round, question, context_hits)
            self._update_summary(conversation, current_round=next_round, last_message=question)
            session.flush()
            interview_session, turn = self._session_and_current_turn(session, conversation)
            yield InterviewStreamEvent(
                event="result",
                data={"session": interview_session, "turn": turn},
            )

        return events()

    def finish_session(self, session: Session, session_id: str) -> tuple[InterviewSessionDTO, ReportDTO]:
        conversation = self._get_active_conversation(session, session_id)
        config = self._conversation_config(conversation)
        turns = self._turns_for_conversation(session, conversation)
        report_result = self.model_service.generate_report(
            ReportGenerationRequest(
                session_id=conversation.id,
                language=config.language,
                turns=[self._turn_report_payload(turn) for turn in turns],
                provider=config.chat_model_provider,
                model=config.chat_model,
            )
        )
        report_markdown = render_interview_report(report_result, config.language)
        writer = self._artifact_writer()
        if writer is None:
            raise bad_gateway("workspace_unavailable", "Workspace services are unavailable.")
        artifacts = writer.archive_finished_session(
            session,
            self._session_response(conversation, config, turns),
            config,
            turns,
            report_markdown,
        )
        report = ReportDTO(
            id=artifacts.report.front_matter.id,
            session_id=conversation.id,
            report_path=artifacts.report_path,
            summary=self.report_summary(
                config.target_role,
                config.target_company,
                config.extra_prompt,
                config.language,
            ),
            weaknesses=self.report_weaknesses(turns),
            created_at=artifacts.report.front_matter.created_at,
        )
        conversation.status = "completed"
        ended_at = datetime.now(UTC)
        self._update_summary(
            conversation,
            current_round=self._current_round_value(conversation),
            last_message=report.summary,
            ended_at=ended_at.isoformat().replace("+00:00", "Z"),
            report_path=report.report_path,
            report_artifact_id=report.id,
            report_summary=report.summary,
            weaknesses=report.weaknesses,
        )
        session.flush()
        finished_session = self._session_response(conversation, config, turns)
        finished_session.status = "completed"
        finished_session.ended_at = ended_at
        finished_session.report_path = report.report_path
        return finished_session, report

    def stream_finish_session(
        self, session: Session, session_id: str
    ) -> Iterator[InterviewStreamEvent]:
        def events() -> Iterator[InterviewStreamEvent]:
            interview_session, report = self.finish_session(session, session_id)
            yield InterviewStreamEvent(event="delta", data={"text": report.summary})
            yield InterviewStreamEvent(
                event="result",
                data={"session": interview_session, "report": report},
            )

        return events()

    def _default_config(self) -> InterviewConfigIn:
        provider = default_chat_provider(self.settings) or preferred_chat_provider(self.settings)
        return InterviewConfigIn(
            target_company="",
            target_role="",
            job_description="",
            extra_prompt="",
            language="en",
            mode="comprehensive",
            chat_model_provider=provider.name,
            chat_model=provider.models[0] if provider.models else "",
            target_rounds=3,
        )

    def _config_payload(
        self,
        config: InterviewConfigIn | InterviewConfigDTO,
        *,
        config_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> dict[str, object]:
        if isinstance(config, InterviewConfigDTO):
            base = {
                "target_company": config.target_company,
                "target_role": config.target_role,
                "job_description": config.job_description,
                "extra_prompt": config.extra_prompt,
                "language": config.language,
                "mode": config.mode,
                "chat_model_provider": config.chat_model_provider,
                "chat_model": config.chat_model,
                "target_rounds": config.target_rounds,
            }
            existing_id = config.id
        else:
            base = config.model_dump()
            existing_id = ""
        timestamp = updated_at or datetime.now(UTC)
        return {
            "id": config_id or existing_id or str(uuid4()),
            **base,
            "updated_at": timestamp.isoformat().replace("+00:00", "Z"),
        }

    def _config_from_dict(
        self,
        payload: dict[str, object],
        *,
        is_last_used: bool,
    ) -> InterviewConfigDTO:
        defaults = self._default_config()
        updated_at = payload.get("updated_at")
        timestamp = (
            datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            if isinstance(updated_at, str) and updated_at
            else datetime.now(UTC)
        )
        return InterviewConfigDTO(
            id=str(payload.get("id") or uuid4()),
            target_company=str(payload.get("target_company") or defaults.target_company),
            target_role=str(payload.get("target_role") or defaults.target_role),
            job_description=str(payload.get("job_description") or defaults.job_description),
            extra_prompt=str(payload.get("extra_prompt") or defaults.extra_prompt),
            language=str(payload.get("language") or defaults.language),
            mode=str(payload.get("mode") or defaults.mode),
            chat_model_provider=str(
                payload.get("chat_model_provider") or defaults.chat_model_provider
            ),
            chat_model=str(payload.get("chat_model") or defaults.chat_model),
            target_rounds=int(payload.get("target_rounds") or defaults.target_rounds),
            is_last_used=is_last_used,
            updated_at=timestamp,
        )

    def _conversation_config(self, conversation: models.Conversation) -> InterviewConfigDTO:
        return self._config_from_dict(conversation.config_json or {}, is_last_used=False)

    def _question_request(
        self,
        config: InterviewConfigIn | InterviewConfigDTO,
        context: list[str],
        *,
        extra_prompt: str | None = None,
    ) -> QuestionGenerationRequest:
        return QuestionGenerationRequest(
            target_company=config.target_company,
            target_role=config.target_role,
            job_description=config.job_description,
            extra_prompt=config.extra_prompt if extra_prompt is None else extra_prompt,
            language=config.language,
            mode=config.mode,
            context=context,
            provider=config.chat_model_provider,
            model=config.chat_model,
        )

    def _answer_request(
        self,
        session: Session,
        config: InterviewConfigDTO,
        *,
        question: str,
        answer: str,
        retrieval_purpose: RetrievalPurpose = "answer_feedback",
    ) -> AnswerEvaluationRequest:
        return AnswerEvaluationRequest(
            question=question,
            answer=answer,
            language=config.language,
            context=self._answer_context(
                session,
                config,
                question=question,
                answer=answer,
                retrieval_purpose=retrieval_purpose,
            ),
            provider=config.chat_model_provider,
            model=config.chat_model,
        )

    def _add_question_message(
        self,
        session: Session,
        conversation_id: str,
        round_index: int,
        question: str,
        context_hits: list[dict[str, object]],
    ) -> models.Message:
        return self.conversations.add_message(
            session,
            user_id=self.user_id,
            conversation_id=conversation_id,
            role="assistant",
            message_type="interview_question",
            content=question,
            metadata_json={
                "round_index": round_index,
                "retrieved_context_refs": [
                    {
                        "source_id": str(hit["source_id"]),
                        "source_type": str(hit["source_type"]),
                    }
                    for hit in context_hits
                ],
            },
        )

    def _store_main_evaluation(
        self,
        session: Session,
        conversation: models.Conversation,
        turn: InterviewTurnDTO,
        answer: str,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        self.conversations.add_message(
            session,
            user_id=self.user_id,
            conversation_id=conversation.id,
            role="user",
            message_type="interview_answer",
            content=answer,
            metadata_json={"round_index": turn.round_index},
        )
        self.conversations.add_message(
            session,
            user_id=self.user_id,
            conversation_id=conversation.id,
            role="assistant",
            message_type="interview_feedback",
            content=evaluation.feedback,
            metadata_json={
                "round_index": turn.round_index,
                "evaluation": evaluation.model_dump(mode="json"),
            },
        )
        if evaluation.follow_up_question:
            self.conversations.add_message(
                session,
                user_id=self.user_id,
                conversation_id=conversation.id,
                role="assistant",
                message_type="interview_follow_up_question",
                content=evaluation.follow_up_question,
                metadata_json={"round_index": turn.round_index},
            )
        self._record_answer_evaluation(session, conversation, turn, turn.question, evaluation)
        self._update_summary(conversation, last_message=evaluation.feedback)
        session.flush()

    def _store_follow_up_evaluation(
        self,
        session: Session,
        conversation: models.Conversation,
        turn: InterviewTurnDTO,
        answer: str,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        self.conversations.add_message(
            session,
            user_id=self.user_id,
            conversation_id=conversation.id,
            role="user",
            message_type="interview_follow_up_answer",
            content=answer,
            metadata_json={"round_index": turn.round_index},
        )
        self.conversations.add_message(
            session,
            user_id=self.user_id,
            conversation_id=conversation.id,
            role="assistant",
            message_type="interview_follow_up_feedback",
            content=evaluation.feedback,
            metadata_json={
                "round_index": turn.round_index,
                "evaluation": evaluation.model_dump(mode="json"),
            },
        )
        self._record_answer_evaluation(
            session,
            conversation,
            turn,
            turn.follow_up_question or turn.question,
            evaluation,
        )
        self._update_summary(conversation, last_message=evaluation.feedback)
        session.flush()

    def _turns_for_conversation(
        self,
        session: Session,
        conversation: models.Conversation,
    ) -> list[InterviewTurnDTO]:
        messages = self.conversations.list_messages(
            session,
            user_id=self.user_id,
            conversation_id=conversation.id,
        )
        turns: dict[int, InterviewTurnDTO] = {}
        for message in messages:
            round_index = self._message_round(message)
            if round_index < 1:
                continue
            if message.message_type == "interview_question":
                turns[round_index] = InterviewTurnDTO(
                    id=message.id,
                    session_id=conversation.id,
                    round_index=round_index,
                    question=message.content,
                    retrieved_context_refs=self._context_refs(message),
                    created_at=message.created_at,
                )
                continue
            turn = turns.get(round_index)
            if turn is None:
                turn = InterviewTurnDTO(
                    id=message.id,
                    session_id=conversation.id,
                    round_index=round_index,
                    question="",
                    created_at=message.created_at,
                )
                turns[round_index] = turn
            self._apply_message_to_turn(turn, message)
        return [turns[index] for index in sorted(turns)]

    def _apply_message_to_turn(self, turn: InterviewTurnDTO, message: models.Message) -> None:
        metadata = message.metadata_json or {}
        evaluation = metadata.get("evaluation")
        if message.message_type == "interview_answer":
            turn.answer = message.content
        elif message.message_type == "interview_feedback":
            turn.feedback = message.content
            self._apply_evaluation_fields(turn, evaluation, follow_up=False)
        elif message.message_type == "interview_follow_up_question":
            turn.follow_up_question = message.content
        elif message.message_type == "interview_follow_up_answer":
            turn.follow_up_answer = message.content
        elif message.message_type == "interview_follow_up_feedback":
            turn.follow_up_feedback = message.content
            self._apply_evaluation_fields(turn, evaluation, follow_up=True)

    def _apply_evaluation_fields(
        self,
        turn: InterviewTurnDTO,
        evaluation: object,
        *,
        follow_up: bool,
    ) -> None:
        if not isinstance(evaluation, dict):
            return
        result = AnswerEvaluationResult.model_validate(evaluation)
        if follow_up:
            turn.follow_up_missing_points = result.missing_points
            turn.follow_up_weaknesses = result.weaknesses
            turn.follow_up_review_suggestions = result.review_suggestions
            turn.follow_up_better_answer = result.better_answer
            turn.follow_up_mastery_change = result.mastery_change
            turn.follow_up_should_write_weakness = result.should_write_weakness
            turn.follow_up_should_write_high_frequency = result.should_write_high_frequency
            turn.follow_up_tested_points = result.tested_points
            return
        turn.missing_points = result.missing_points
        turn.follow_up_question = result.follow_up_question or turn.follow_up_question
        turn.weaknesses = result.weaknesses
        turn.review_suggestions = result.review_suggestions
        turn.better_answer = result.better_answer
        turn.mastery_change = result.mastery_change
        turn.should_write_weakness = result.should_write_weakness
        turn.should_write_high_frequency = result.should_write_high_frequency
        turn.tested_points = result.tested_points

    def _message_round(self, message: models.Message) -> int:
        value = (message.metadata_json or {}).get("round_index")
        return value if isinstance(value, int) else 0

    def _context_refs(self, message: models.Message) -> list[dict[str, str]]:
        value = (message.metadata_json or {}).get("retrieved_context_refs")
        if not isinstance(value, list):
            return []
        refs = []
        for item in value:
            if isinstance(item, dict):
                refs.append(
                    {
                        "source_id": str(item.get("source_id", "")),
                        "source_type": str(item.get("source_type", "")),
                    }
                )
        return refs

    def _session_response(
        self,
        conversation: models.Conversation,
        config: InterviewConfigDTO,
        turns: list[InterviewTurnDTO],
    ) -> InterviewSessionDTO:
        summary = conversation.summary_json or {}
        ended_at = self._optional_datetime(summary.get("ended_at"))
        current_round = self._current_round_value(conversation)
        response = InterviewSessionDTO(
            id=conversation.id,
            config_id=config.id,
            status=conversation.status,
            current_round=current_round,
            started_at=conversation.created_at,
            ended_at=ended_at,
            report_path=self._optional_str(summary.get("report_path")),
            config=config,
            turns=turns,
        )
        return response

    def _session_and_current_turn(
        self,
        session: Session,
        conversation: models.Conversation,
    ) -> tuple[InterviewSessionDTO, InterviewTurnDTO]:
        config = self._conversation_config(conversation)
        turns = self._turns_for_conversation(session, conversation)
        interview_session = self._session_response(conversation, config, turns)
        return interview_session, self._turn_for_round(turns, interview_session.current_round)

    def _turn_for_round(
        self,
        turns: list[InterviewTurnDTO],
        round_index: int,
    ) -> InterviewTurnDTO:
        for turn in reversed(turns):
            if turn.round_index == round_index:
                return turn
        raise not_found("turn_not_found", "Current interview turn not found.")

    def _current_turn(
        self,
        session: Session,
        conversation: models.Conversation,
    ) -> InterviewTurnDTO:
        return self._turn_for_round(
            self._turns_for_conversation(session, conversation),
            self._current_round_value(conversation),
        )

    def _current_round_value(self, conversation: models.Conversation) -> int:
        value = (conversation.summary_json or {}).get("current_round")
        return value if isinstance(value, int) and value > 0 else 1

    def _get_active_conversation(
        self,
        session: Session,
        session_id: str,
    ) -> models.Conversation:
        conversation = self.conversations.get(
            session,
            user_id=self.user_id,
            conversation_id=session_id,
            kind="interview",
        )
        if conversation is None:
            raise not_found("session_not_found", "Interview session not found.")
        if conversation.status != "active":
            raise conflict("session_not_active", "Interview session is not active.")
        return conversation

    def _update_summary(self, conversation: models.Conversation, **updates: object) -> None:
        conversation.summary_json = {**(conversation.summary_json or {}), **updates}
        conversation.updated_at = models._now()

    def _config_from_natural_intent(self, config_in: InterviewConfigIn) -> InterviewConfigIn:
        intent = config_in.extra_prompt.strip()
        if not intent:
            return config_in
        updates: dict[str, Any] = {"mode": self._infer_mode(intent, config_in.mode)}
        inferred_rounds = self._infer_round_count(intent)
        if inferred_rounds is not None:
            updates["target_rounds"] = inferred_rounds
        return config_in.model_copy(update=updates)

    def _infer_mode(self, intent: str, fallback: str) -> str:
        lowered = intent.lower()
        if any(marker in lowered for marker in ("project", "项目", "深挖", "经历")):
            return "project_deep_dive"
        if any(marker in lowered for marker in ("weak", "薄弱", "错题", "答差", "不会")):
            return "weakness_reinforcement"
        if any(marker in lowered for marker in ("knowledge", "知识", "八股", "基础", "考点")):
            return "knowledge_drill"
        return fallback

    def _infer_round_count(self, intent: str) -> int | None:
        normalized_digits = {
            "一": 1,
            "两": 2,
            "二": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
        }
        match = re.search(r"(\d{1,2})\s*(?:轮|题|道|round|rounds|questions?)", intent, re.IGNORECASE)
        if match:
            return max(1, min(12, int(match.group(1))))
        for marker, value in normalized_digits.items():
            if re.search(rf"{marker}\s*(?:轮|题|道)", intent):
                return value
        lowered = intent.lower()
        if "考考我" in intent or "抽检" in intent or "quiz me" in lowered:
            return 5
        return None

    def _apply_next_intent(
        self,
        conversation: models.Conversation,
        config: InterviewConfigDTO,
        intent: str,
    ) -> str:
        cleaned = intent.strip()
        if not cleaned:
            return config.extra_prompt
        config.extra_prompt = self._merged_intent(config.extra_prompt, cleaned)
        config.mode = self._infer_mode(cleaned, config.mode)
        inferred_rounds = self._infer_round_count(cleaned)
        if inferred_rounds is not None:
            config.target_rounds += inferred_rounds
        conversation.config_json = self._config_payload(config, config_id=config.id)
        return config.extra_prompt

    def _merged_intent(self, existing: str, intent: str) -> str:
        cleaned = intent.strip()
        if not cleaned:
            return existing
        existing_lines = [line.strip() for line in existing.splitlines() if line.strip()]
        if cleaned in existing_lines:
            return existing
        return "\n".join([*existing_lines, cleaned])[-4000:]

    def _stream_question_events(
        self, request: QuestionGenerationRequest
    ) -> Iterator[InterviewStreamEvent]:
        question = ""
        for chunk in self.model_service.stream_question(request):
            question += chunk
            yield InterviewStreamEvent(event="delta", data={"text": chunk})
        question = question.strip()
        if not question:
            raise bad_gateway(
                "provider_invalid_response",
                "The selected model returned an empty question.",
            )
        return question

    def _stream_answer_evaluation_events(
        self, request: AnswerEvaluationRequest
    ) -> Iterator[InterviewStreamEvent]:
        raw_content = ""
        preview = ""
        for chunk in self.model_service.stream_answer_evaluation(request):
            raw_content += chunk
            next_preview = self._preview_answer_json(raw_content)
            delta = self._preview_delta(preview, next_preview)
            if delta:
                yield InterviewStreamEvent(event="delta", data={"text": delta})
                preview = next_preview

        evaluation = self.model_service.parse_answer_evaluation(raw_content)
        final_preview = self._format_answer_preview(evaluation, request.language)
        delta = self._preview_delta(preview, final_preview)
        if delta:
            yield InterviewStreamEvent(event="delta", data={"text": delta})
        return evaluation

    def _preview_answer_json(self, content: str) -> str:
        cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip())
        feedback = self._extract_partial_json_string(cleaned, "feedback")
        if feedback:
            return feedback
        return ""

    def _extract_partial_json_string(self, content: str, key: str) -> str:
        marker = f'"{key}"'
        start = content.find(marker)
        if start < 0:
            return ""
        colon = content.find(":", start + len(marker))
        if colon < 0:
            return ""
        quote = content.find('"', colon + 1)
        if quote < 0:
            return ""

        value: list[str] = []
        escaped = False
        for char in content[quote + 1 :]:
            if escaped:
                value.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                break
            value.append(char)
        return "".join(value)

    def _format_answer_preview(
        self,
        evaluation: AnswerEvaluationResult,
        language: str = "en",
    ) -> str:
        return render_answer_preview(evaluation, language)

    def _preview_delta(self, previous: str, current: str) -> str:
        if not current or current == previous:
            return ""
        if current.startswith(previous):
            return current[len(previous) :]
        return current

    def _answer_context(
        self,
        session: Session,
        config: InterviewConfigDTO,
        *,
        question: str,
        answer: str,
        retrieval_purpose: RetrievalPurpose = "answer_feedback",
    ) -> list[str]:
        direct_context = self._direct_workspace_context(session)
        project_context = self._project_context(session)
        context_hits = self.retrieval_service.search(
            session,
            RetrievalRequest(
                purpose=retrieval_purpose,
                query=_answer_context_query(
                    target_company=config.target_company,
                    target_role=config.target_role,
                    job_description=config.job_description,
                    extra_prompt=config.extra_prompt,
                    question=question,
                    answer=answer,
                ),
                mode=config.mode,
                limit=4,
            ),
        )
        retrieved_context = [self._retrieved_context_text(hit) for hit in context_hits]
        return self.context_assembler.assemble(
            direct_context=direct_context,
            project_context=project_context,
            retrieved_context=[
                f"[本题考察点]\n围绕当前题目识别考察点：{question}",
                *retrieved_context,
            ],
        )

    def _question_context(
        self,
        session: Session,
        query: str,
        *,
        mode: str = "comprehensive",
    ) -> tuple[list[str], list[dict[str, object]]]:
        direct_context = self._direct_workspace_context(session)
        project_context = self._project_context(session) if mode == "project_deep_dive" else []
        context_hits = self.retrieval_service.search(
            session,
            RetrievalRequest(
                purpose="question_generation",
                query=query,
                mode=mode,
                limit=4,
            ),
        )
        retrieved_context = [self._retrieved_context_text(hit) for hit in context_hits]
        context = self.context_assembler.assemble(
            direct_context=direct_context,
            project_context=project_context,
            retrieved_context=retrieved_context,
        )
        return context, context_hits

    def _direct_workspace_context(self, session: Session) -> list[str]:
        if self.artifact_service is None:
            return []
        context: list[str] = []
        for relative_path, label in DIRECT_WORKSPACE_CONTEXT_FILES:
            artifact = self.artifact_repository.get_by_relative_path(
                session,
                user_id=self.user_id,
                relative_path=relative_path,
            )
            if artifact is None:
                continue
            try:
                body = self.artifact_service.read_markdown(relative_path).body.strip()
            except Exception:
                continue
            if body:
                context.append(f"[{label} | {relative_path}]\n{body}")
        return context

    def _project_context(self, session: Session) -> list[str]:
        if self.artifact_service is None:
            return []
        context: list[str] = []
        project_artifacts = [
            artifact
            for artifact in self.artifact_repository.list(session, user_id=self.user_id)
            if artifact.kind == "project"
        ][:PROJECT_CONTEXT_LIMIT]
        for artifact in project_artifacts:
            try:
                body = self.artifact_service.read_markdown(artifact.relative_path).body.strip()
            except Exception:
                continue
            if body:
                context.append(f"[项目材料 | {artifact.relative_path}]\n{body}")
        return context

    def _retrieved_context_text(self, hit: dict[str, object]) -> str:
        source_type = str(hit.get("source_type", "artifact"))
        source_id = str(hit.get("source_id", "unknown"))
        return f"[检索片段 | {source_type}:{source_id}]\n{hit.get('content', '')}"

    def _persist_practice_progress(
        self,
        session: Session,
        conversation: models.Conversation,
    ) -> None:
        writer = self._artifact_writer()
        if writer is None:
            return
        config = self._conversation_config(conversation)
        turns = self._turns_for_conversation(session, conversation)
        writer.record_answer_progress(
            session,
            self._session_response(conversation, config, turns),
            config,
            turns,
        )

    def _record_answer_evaluation(
        self,
        session: Session,
        conversation: models.Conversation,
        turn: InterviewTurnDTO,
        question: str,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        writer = self._artifact_writer()
        if writer is None:
            return
        writer.record_answer_evaluation(
            session,
            self._conversation_config(conversation),
            turn,
            question=question,
            evaluation=evaluation,
        )

    def _artifact_writer(self) -> InterviewArtifactService | None:
        if self.interview_artifact_service is not None:
            return self.interview_artifact_service
        if self.artifact_service is None or self.workspace_service is None:
            return None
        self.interview_artifact_service = InterviewArtifactService(
            user_id=self.user_id,
            workspace_service=self.workspace_service,
            artifact_service=self.artifact_service,
            artifact_repository=self.artifact_repository,
        )
        return self.interview_artifact_service

    def _turn_report_payload(self, turn: InterviewTurnDTO) -> dict[str, object]:
        return {
            "round_index": turn.round_index,
            "question": turn.question,
            "answer": turn.answer,
            "feedback": turn.feedback,
            "missing_points": turn.missing_points,
            "follow_up_question": turn.follow_up_question,
            "follow_up_answer": turn.follow_up_answer,
            "follow_up_feedback": turn.follow_up_feedback,
            "follow_up_missing_points": turn.follow_up_missing_points,
            "follow_up_weaknesses": turn.follow_up_weaknesses,
            "follow_up_review_suggestions": turn.follow_up_review_suggestions,
            "weaknesses": turn.weaknesses,
            "review_suggestions": turn.review_suggestions,
        }

    @staticmethod
    def report_weaknesses(turns: list[InterviewTurnDTO]) -> list[str]:
        return sorted(
            {
                weakness
                for turn in turns
                for weakness in [*turn.weaknesses, *turn.follow_up_weaknesses]
            }
        )

    @staticmethod
    def report_summary(
        target_role: str,
        target_company: str,
        extra_prompt: str,
        language: str,
    ) -> str:
        role = target_role.strip()
        company = target_company.strip()
        natural_context = " ".join(extra_prompt.split())[:120]
        if language == "zh-CN":
            if role and company:
                return f"{company}{role}面试复盘"
            if role:
                return f"{role}面试复盘"
            if company:
                return f"{company}面试复盘"
            if natural_context:
                return f"面试复盘：{natural_context}"
            return "面试复盘"
        if role and company:
            return f"{role} interview for {company}"
        if role:
            return f"{role} interview"
        if company:
            return f"Interview for {company}"
        if natural_context:
            return f"Interview review: {natural_context}"
        return "Interview review"

    def _conversation_title(self, config: InterviewConfigIn) -> str:
        title = config.extra_prompt.strip()
        if title:
            return title[:255]
        if config.target_role.strip() and config.target_company.strip():
            return f"{config.target_company.strip()} {config.target_role.strip()}"[:255]
        if config.target_role.strip():
            return config.target_role.strip()[:255]
        return "模拟面试" if config.language == "zh-CN" else "Mock interview"

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _optional_datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
