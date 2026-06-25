import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import bad_gateway, conflict, not_found
from app.db.models import InterviewConfig, InterviewSession, InterviewTurn
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.database import (
    InterviewConfigRepository,
    InterviewSessionRepository,
    InterviewTurnRepository,
)
from app.schemas.interviews import InterviewConfigIn
from app.services.model_service import (
    AnswerEvaluationRequest,
    AnswerEvaluationResult,
    ModelService,
    QuestionGenerationRequest,
)
from app.services.artifact_service import ArtifactService
from app.services.context_assembler import ContextAssembler
from app.services.memory_service import MemoryService
from app.services.retrieval_query_planner import RetrievalPurpose, RetrievalRequest
from app.services.workspace_service import WorkspaceService
from app.services.workspace_retrieval_service import WorkspaceRetrievalService


DIRECT_WORKSPACE_CONTEXT_FILES = (
    ("profile/candidate.md", "候选人画像"),
    ("profile/target.md", "目标画像"),
    ("state/mastery.md", "掌握状态"),
    ("review/status.md", "复习状态"),
    ("review/high-frequency.md", "高频与薄弱点"),
)
PROJECT_CONTEXT_LIMIT = 3


@dataclass
class InterviewStreamEvent:
    event: str
    data: dict[str, Any]


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
        config_repository: InterviewConfigRepository | None = None,
        session_repository: InterviewSessionRepository | None = None,
        turn_repository: InterviewTurnRepository | None = None,
        model_service: ModelService | None = None,
        retrieval_service: WorkspaceRetrievalService | None = None,
        context_assembler: ContextAssembler | None = None,
        artifact_repository: ArtifactRepository | None = None,
        artifact_service: ArtifactService | None = None,
        workspace_service: WorkspaceService | None = None,
    ) -> None:
        self.config_repository = config_repository or InterviewConfigRepository()
        self.session_repository = session_repository or InterviewSessionRepository()
        self.turn_repository = turn_repository or InterviewTurnRepository()
        self.model_service = model_service or ModelService()
        self.retrieval_service = retrieval_service or WorkspaceRetrievalService()
        self.context_assembler = context_assembler or ContextAssembler()
        self.artifact_repository = artifact_repository or ArtifactRepository()
        self.artifact_service = artifact_service
        self.workspace_service = workspace_service

    def get_last_config(self, session: Session) -> InterviewConfig:
        config = self.config_repository.get_last(session)
        if config is not None:
            return config
        return self.save_last_config(
            session,
            InterviewConfigIn(
                target_company="",
                target_role="",
                job_description="",
                extra_prompt="",
                language="en",
                mode="comprehensive",
                chat_model_provider="qwen",
                chat_model="qwen3.7-plus",
                target_rounds=3,
            ),
        )

    def save_last_config(self, session: Session, config_in: InterviewConfigIn) -> InterviewConfig:
        self.config_repository.clear_last_used(session)
        config = InterviewConfig(**config_in.model_dump(), is_last_used=True)
        return self.config_repository.add(session, config)

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

    def create_session(
        self, session: Session, config_in: InterviewConfigIn
    ) -> tuple[InterviewSession, InterviewTurn]:
        config_in = self._config_from_natural_intent(config_in)
        config = InterviewConfig(**config_in.model_dump(), is_last_used=False)
        self.config_repository.add(session, config)
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
        question = self.model_service.generate_question(
            QuestionGenerationRequest(
                target_company=config_in.target_company,
                target_role=config_in.target_role,
                job_description=config_in.job_description,
                extra_prompt=config_in.extra_prompt,
                language=config_in.language,
                mode=config_in.mode,
                context=context,
                provider=config_in.chat_model_provider,
                model=config_in.chat_model,
            )
        )
        interview_session = self.session_repository.add(
            session,
            InterviewSession(config_id=config.id, status="active", current_round=1),
        )
        turn = self.turn_repository.add(
            session,
            InterviewTurn(
                session_id=interview_session.id,
                round_index=1,
                question=question,
                retrieved_context_refs=[
                    {"source_id": str(hit["source_id"]), "source_type": str(hit["source_type"])}
                    for hit in context_hits
                ],
            ),
        )
        return interview_session, turn

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
        request = QuestionGenerationRequest(
            target_company=config_in.target_company,
            target_role=config_in.target_role,
            job_description=config_in.job_description,
            extra_prompt=config_in.extra_prompt,
            language=config_in.language,
            mode=config_in.mode,
            context=context,
            provider=config_in.chat_model_provider,
            model=config_in.chat_model,
        )

        def events() -> Iterator[InterviewStreamEvent]:
            question = yield from self._stream_question_events(request)
            config = self.config_repository.add(
                session,
                InterviewConfig(**config_in.model_dump(), is_last_used=False),
            )
            interview_session = self.session_repository.add(
                session,
                InterviewSession(config_id=config.id, status="active", current_round=1),
            )
            turn = self.turn_repository.add(
                session,
                InterviewTurn(
                    session_id=interview_session.id,
                    round_index=1,
                    question=question,
                    retrieved_context_refs=[
                        {
                            "source_id": str(hit["source_id"]),
                            "source_type": str(hit["source_type"]),
                        }
                        for hit in context_hits
                    ],
                ),
            )
            yield InterviewStreamEvent(
                event="result",
                data={"session": interview_session, "turn": turn},
            )

        return events()

    def get_session_detail(
        self, session: Session, session_id: str
    ) -> tuple[InterviewSession, InterviewConfig, list[InterviewTurn]] | None:
        interview_session = self.session_repository.get(session, session_id)
        if interview_session is None:
            return None
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        turns = self.turn_repository.list_for_session(session, session_id)
        return interview_session, config, turns

    def list_session_details(
        self, session: Session, *, limit: int = 50
    ) -> list[tuple[InterviewSession, InterviewConfig, list[InterviewTurn], bool]]:
        details: list[tuple[InterviewSession, InterviewConfig, list[InterviewTurn], bool]] = []
        for interview_session in self.session_repository.list_recent(session, limit=limit):
            config = session.get(InterviewConfig, interview_session.config_id)
            if config is None:
                continue
            turns = self.turn_repository.list_for_session(session, interview_session.id)
            details.append(
                (
                    interview_session,
                    config,
                    turns,
                    interview_session.status == "active",
                )
            )
        return details

    def submit_answer(
        self, session: Session, session_id: str, answer: str
    ) -> AnswerEvaluationResult:
        interview_session = self._get_active_session(session, session_id)
        turn = self._current_turn(session, interview_session)
        if turn.answer is not None:
            raise conflict("answer_already_submitted", "The current answer was already submitted.")
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        context = self._answer_context(
            session,
            config,
            question=turn.question,
            answer=answer,
        )
        evaluation = self.model_service.evaluate_answer(
            AnswerEvaluationRequest(
                question=turn.question,
                answer=answer,
                language=config.language,
                context=context,
                provider=config.chat_model_provider,
                model=config.chat_model,
            )
        )
        turn.answer = answer
        turn.feedback = evaluation.feedback
        turn.missing_points = evaluation.missing_points
        turn.follow_up_question = evaluation.follow_up_question
        turn.weaknesses = evaluation.weaknesses
        turn.review_suggestions = evaluation.review_suggestions
        self._upsert_question_bank_entry(
            session,
            config,
            turn,
            question=turn.question,
            evaluation=evaluation,
        )
        self._upsert_high_frequency_question(config, turn.question, evaluation)
        session.flush()
        self._persist_practice_progress(session, interview_session, config)
        return evaluation

    def stream_submit_answer(
        self, session: Session, session_id: str, answer: str
    ) -> Iterator[InterviewStreamEvent]:
        interview_session = self._get_active_session(session, session_id)
        turn = self._current_turn(session, interview_session)
        if turn.answer is not None:
            raise conflict("answer_already_submitted", "The current answer was already submitted.")
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        context = self._answer_context(
            session,
            config,
            question=turn.question,
            answer=answer,
        )
        request = AnswerEvaluationRequest(
            question=turn.question,
            answer=answer,
            language=config.language,
            context=context,
            provider=config.chat_model_provider,
            model=config.chat_model,
        )

        def events() -> Iterator[InterviewStreamEvent]:
            evaluation = yield from self._stream_answer_evaluation_events(request)
            turn.answer = answer
            turn.feedback = evaluation.feedback
            turn.missing_points = evaluation.missing_points
            turn.follow_up_question = evaluation.follow_up_question
            turn.weaknesses = evaluation.weaknesses
            turn.review_suggestions = evaluation.review_suggestions
            self._upsert_question_bank_entry(
                session,
                config,
                turn,
                question=turn.question,
                evaluation=evaluation,
            )
            self._upsert_high_frequency_question(config, turn.question, evaluation)
            session.flush()
            self._persist_practice_progress(session, interview_session, config)
            yield InterviewStreamEvent(event="result", data=evaluation.model_dump())

        return events()

    def submit_follow_up_answer(
        self, session: Session, session_id: str, answer: str
    ) -> AnswerEvaluationResult:
        interview_session = self._get_active_session(session, session_id)
        turn = self._current_turn(session, interview_session)
        if turn.answer is None or not turn.follow_up_question:
            raise conflict("main_answer_required", "Submit the main answer before the follow-up.")
        if turn.follow_up_answer is not None:
            raise conflict(
                "follow_up_already_submitted",
                "The current follow-up answer was already submitted.",
            )
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        context = self._answer_context(
            session,
            config,
            question=turn.follow_up_question,
            answer=answer,
            retrieval_purpose="follow_up_feedback",
        )
        evaluation = self.model_service.evaluate_answer(
            AnswerEvaluationRequest(
                question=turn.follow_up_question,
                answer=answer,
                language=config.language,
                context=context,
                provider=config.chat_model_provider,
                model=config.chat_model,
            )
        )
        turn.follow_up_answer = answer
        turn.follow_up_feedback = evaluation.feedback
        turn.follow_up_missing_points = evaluation.missing_points
        turn.follow_up_weaknesses = evaluation.weaknesses
        turn.follow_up_review_suggestions = evaluation.review_suggestions
        self._upsert_question_bank_entry(
            session,
            config,
            turn,
            question=turn.follow_up_question,
            evaluation=evaluation,
        )
        self._upsert_high_frequency_question(config, turn.follow_up_question, evaluation)
        session.flush()
        self._persist_practice_progress(session, interview_session, config)
        return evaluation

    def stream_submit_follow_up_answer(
        self, session: Session, session_id: str, answer: str
    ) -> Iterator[InterviewStreamEvent]:
        interview_session = self._get_active_session(session, session_id)
        turn = self._current_turn(session, interview_session)
        if turn.answer is None or not turn.follow_up_question:
            raise conflict("main_answer_required", "Submit the main answer before the follow-up.")
        if turn.follow_up_answer is not None:
            raise conflict(
                "follow_up_already_submitted",
                "The current follow-up answer was already submitted.",
            )
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        context = self._answer_context(
            session,
            config,
            question=turn.follow_up_question,
            answer=answer,
            retrieval_purpose="follow_up_feedback",
        )
        request = AnswerEvaluationRequest(
            question=turn.follow_up_question,
            answer=answer,
            language=config.language,
            context=context,
            provider=config.chat_model_provider,
            model=config.chat_model,
        )

        def events() -> Iterator[InterviewStreamEvent]:
            evaluation = yield from self._stream_answer_evaluation_events(request)
            turn.follow_up_answer = answer
            turn.follow_up_feedback = evaluation.feedback
            turn.follow_up_missing_points = evaluation.missing_points
            turn.follow_up_weaknesses = evaluation.weaknesses
            turn.follow_up_review_suggestions = evaluation.review_suggestions
            self._upsert_question_bank_entry(
                session,
                config,
                turn,
                question=turn.follow_up_question,
                evaluation=evaluation,
            )
            self._upsert_high_frequency_question(config, turn.follow_up_question, evaluation)
            session.flush()
            self._persist_practice_progress(session, interview_session, config)
            yield InterviewStreamEvent(event="result", data=evaluation.model_dump())

        return events()

    def next_question(
        self, session: Session, session_id: str, *, intent: str = ""
    ) -> tuple[InterviewSession, InterviewTurn]:
        interview_session = self._get_active_session(session, session_id)
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        current_turn = self._current_turn(session, interview_session)
        if current_turn.answer is None:
            raise conflict("current_turn_unanswered", "Answer the current question before continuing.")
        next_round = interview_session.current_round + 1
        next_extra_prompt = self._apply_next_intent(config, intent)
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
        question = self.model_service.generate_question(
            QuestionGenerationRequest(
                target_company=config.target_company,
                target_role=config.target_role,
                job_description=config.job_description,
                extra_prompt=next_extra_prompt,
                language=config.language,
                mode=config.mode,
                context=context,
                provider=config.chat_model_provider,
                model=config.chat_model,
            )
        )
        interview_session.current_round = next_round
        turn = self.turn_repository.add(
            session,
            InterviewTurn(
                session_id=interview_session.id,
                round_index=next_round,
                question=question,
                retrieved_context_refs=[
                    {"source_id": str(hit["source_id"]), "source_type": str(hit["source_type"])}
                    for hit in context_hits
                ],
            ),
        )
        session.flush()
        return interview_session, turn

    def stream_next_question(
        self, session: Session, session_id: str, *, intent: str = ""
    ) -> Iterator[InterviewStreamEvent]:
        interview_session = self._get_active_session(session, session_id)
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        current_turn = self._current_turn(session, interview_session)
        if current_turn.answer is None:
            raise conflict("current_turn_unanswered", "Answer the current question before continuing.")
        next_round = interview_session.current_round + 1
        next_extra_prompt = self._apply_next_intent(config, intent)
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
        request = QuestionGenerationRequest(
            target_company=config.target_company,
            target_role=config.target_role,
            job_description=config.job_description,
            extra_prompt=next_extra_prompt,
            language=config.language,
            mode=config.mode,
            context=context,
            provider=config.chat_model_provider,
            model=config.chat_model,
        )

        def events() -> Iterator[InterviewStreamEvent]:
            question = yield from self._stream_question_events(request)
            interview_session.current_round = next_round
            turn = self.turn_repository.add(
                session,
                InterviewTurn(
                    session_id=interview_session.id,
                    round_index=next_round,
                    question=question,
                    retrieved_context_refs=[
                        {
                            "source_id": str(hit["source_id"]),
                            "source_type": str(hit["source_type"]),
                        }
                        for hit in context_hits
                    ],
                ),
            )
            session.flush()
            yield InterviewStreamEvent(
                event="result",
                data={"session": interview_session, "turn": turn},
            )

        return events()

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
        final_preview = self._format_answer_preview(evaluation)
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

    def _format_answer_preview(self, evaluation: AnswerEvaluationResult) -> str:
        sections = [evaluation.feedback.strip()]
        if evaluation.missing_points:
            sections.append(
                "Missing points\n" + "\n".join(f"- {point}" for point in evaluation.missing_points)
            )
        if evaluation.weaknesses:
            sections.append(
                "Weaknesses\n" + "\n".join(f"- {weakness}" for weakness in evaluation.weaknesses)
            )
        if evaluation.review_suggestions:
            sections.append(
                "Review suggestions\n"
                + "\n".join(f"- {suggestion}" for suggestion in evaluation.review_suggestions)
            )
        if evaluation.better_answer:
            sections.append("Better answer\n" + evaluation.better_answer.strip())
        if evaluation.tested_points:
            sections.append(
                "Tested points\n" + "\n".join(f"- {point}" for point in evaluation.tested_points)
            )
        sections.append(f"Mastery change\n{evaluation.mastery_change}")
        if evaluation.should_write_weakness or evaluation.should_write_high_frequency:
            write_flags = []
            if evaluation.should_write_weakness:
                write_flags.append("write weakness")
            if evaluation.should_write_high_frequency:
                write_flags.append("write high-frequency question")
            sections.append("Persistence suggestion\n" + ", ".join(write_flags))
        if evaluation.follow_up_question:
            sections.append("Follow-up\n" + evaluation.follow_up_question.strip())
        return "\n\n".join(section for section in sections if section)

    def _preview_delta(self, previous: str, current: str) -> str:
        if not current or current == previous:
            return ""
        if current.startswith(previous):
            return current[len(previous) :]
        return current

    def _answer_context(
        self,
        session: Session,
        config: InterviewConfig,
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
            artifact = self.artifact_repository.get_by_relative_path(session, relative_path)
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
            for artifact in self.artifact_repository.list(session)
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

    def _apply_next_intent(self, config: InterviewConfig, intent: str) -> str:
        cleaned = intent.strip()
        if not cleaned:
            return config.extra_prompt
        config.extra_prompt = self._merged_intent(config.extra_prompt, cleaned)
        config.mode = self._infer_mode(cleaned, config.mode)
        inferred_rounds = self._infer_round_count(cleaned)
        if inferred_rounds is not None:
            config.target_rounds += inferred_rounds
        return config.extra_prompt

    def _merged_intent(self, existing: str, intent: str) -> str:
        cleaned = intent.strip()
        if not cleaned:
            return existing
        existing_lines = [line.strip() for line in existing.splitlines() if line.strip()]
        if cleaned in existing_lines:
            return existing
        return "\n".join([*existing_lines, cleaned])[-4000:]

    def _retrieved_context_text(self, hit: dict[str, object]) -> str:
        source_type = str(hit.get("source_type", "artifact"))
        source_id = str(hit.get("source_id", "unknown"))
        return f"[检索片段 | {source_type}:{source_id}]\n{hit.get('content', '')}"

    def _persist_practice_progress(
        self,
        session: Session,
        interview_session: InterviewSession,
        config: InterviewConfig,
    ) -> None:
        if self.artifact_service is None or self.workspace_service is None:
            return
        turns = self.turn_repository.list_for_session(session, interview_session.id)
        if not any(turn.answer or turn.follow_up_answer for turn in turns):
            return

        language = config.language or "zh-CN"
        started = interview_session.started_at.astimezone(UTC)
        practice_path = f"practice/{started:%Y-%m-%d}.md"
        session_heading = f"会话 {interview_session.id}"
        session_body = self._practice_session_body(interview_session, config, turns)
        evidence_ref = f"interview_session:{interview_session.id}"
        try:
            current = self.artifact_service.read_markdown(practice_path)
        except FileNotFoundError:
            self.artifact_service.create_markdown(
                practice_path,
                kind="practice",
                body=f"# 模拟面试记录\n\n## {session_heading}\n\n{session_body}\n",
                language=language,
                origin="observed",
                edited_by="system",
                evidence_refs=[evidence_ref],
            )
        else:
            body = self._replace_or_append_h2(current.body, session_heading, session_body)
            self.artifact_service.replace_body(
                practice_path,
                expected_revision=current.front_matter.revision,
                body=body,
                edited_by="system",
                now=datetime.now(UTC),
            )
        self._upsert_review_status(config, turns, evidence_ref)
        self.workspace_service.rebuild_projection(
            session,
            self.artifact_repository,
            self.artifact_service,
        )

    def _practice_session_body(
        self,
        interview_session: InterviewSession,
        config: InterviewConfig,
        turns: list[InterviewTurn],
    ) -> str:
        body = [
            f"- 开始时间：{interview_session.started_at.astimezone(UTC).isoformat().replace('+00:00', 'Z')}",
            f"- 出题要求：{config.extra_prompt.strip() or '默认抽检'}",
            "",
        ]
        for turn in turns:
            body.extend(
                [
                    f"### 第 {turn.round_index} 轮",
                    "",
                    f"**问题**：{turn.question}",
                    "",
                    f"**回答**：{turn.answer or ''}",
                    "",
                    f"**点评**：{turn.feedback or ''}",
                    "",
                ]
            )
            if turn.missing_points:
                body.extend(["**缺失点**：", self._bullet_list(turn.missing_points), ""])
            if turn.weaknesses:
                body.extend(["**薄弱点**：", self._bullet_list(turn.weaknesses), ""])
            if turn.review_suggestions:
                body.extend(["**复习建议**：", self._bullet_list(turn.review_suggestions), ""])
            if turn.follow_up_question:
                body.extend(
                    [
                        f"**追问**：{turn.follow_up_question}",
                        "",
                        f"**追问回答**：{turn.follow_up_answer or ''}",
                        "",
                        f"**追问点评**：{turn.follow_up_feedback or ''}",
                        "",
                    ]
                )
                if turn.follow_up_missing_points:
                    body.extend(["**追问缺失点**：", self._bullet_list(turn.follow_up_missing_points), ""])
                if turn.follow_up_weaknesses:
                    body.extend(["**追问薄弱点**：", self._bullet_list(turn.follow_up_weaknesses), ""])
                if turn.follow_up_review_suggestions:
                    body.extend(["**追问复习建议**：", self._bullet_list(turn.follow_up_review_suggestions), ""])
        return "\n".join(body).strip()

    def _upsert_review_status(
        self,
        config: InterviewConfig,
        turns: list[InterviewTurn],
        evidence_ref: str,
    ) -> None:
        if self.artifact_service is None:
            return
        status_path = "review/status.md"
        focus = self._top_items(
            [
                item
                for turn in turns
                for item in [
                    *turn.weaknesses,
                    *turn.follow_up_weaknesses,
                    *turn.missing_points,
                    *turn.follow_up_missing_points,
                    *turn.review_suggestions,
                    *turn.follow_up_review_suggestions,
                ]
            ],
            6,
        )
        latest_question = next((turn.question for turn in reversed(turns) if turn.answer), "")
        latest_practice = f"练习：{latest_question}" if latest_question else ""
        try:
            current = self.artifact_service.read_markdown(status_path)
            sections = self._markdown_sections(current.body)
            recent_learning = self._markdown_list_items(sections.get("最近整理") or "")
            recent_practice = self._markdown_list_items(sections.get("最近练习") or "")
            evidence_refs = self._top_items([*current.front_matter.evidence_refs, evidence_ref], 20)
        except FileNotFoundError:
            current = None
            recent_learning = []
            recent_practice = []
            evidence_refs = [evidence_ref]

        practice_items = self._top_items([latest_practice, *recent_practice], 8)
        body = (
            "# 复习状态\n\n"
            "## 当前重点\n\n"
            f"{self._plain_bullet_list(focus or ['继续通过模拟面试暴露薄弱点'])}\n\n"
            "## 最近整理\n\n"
            f"{self._plain_bullet_list(recent_learning)}\n\n"
            "## 最近练习\n\n"
            f"{self._plain_bullet_list(practice_items)}\n"
        )
        if current is None:
            self.artifact_service.create_markdown(
                status_path,
                kind="review_status",
                body=body,
                language=config.language or "zh-CN",
                origin="llm",
                edited_by="system",
                evidence_refs=evidence_refs,
            )
            return
        self.artifact_service.replace_body(
            status_path,
            expected_revision=current.front_matter.revision,
            body=body,
            edited_by="system",
            now=datetime.now(UTC),
        )

    def _upsert_high_frequency_question(
        self,
        config: InterviewConfig,
        question: str | None,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        if self.artifact_service is None or not evaluation.should_write_high_frequency or not question:
            return
        relative_path = "review/high-frequency.md"
        try:
            current = self.artifact_service.read_markdown(relative_path)
            sections = self._markdown_sections(current.body)
            existing_questions = self._markdown_list_items(sections.get("真实面试高频问题") or "")
            existing_weak_points = self._markdown_list_items(sections.get("暴露问题") or "")
        except FileNotFoundError:
            current = None
            existing_questions = []
            existing_weak_points = []

        questions = self._top_items([question, *existing_questions], 20)
        weak_points = self._top_items(
            [*evaluation.weaknesses, *evaluation.missing_points, *existing_weak_points],
            20,
        )
        body = (
            "# 高频与薄弱点\n\n"
            "## 真实面试高频问题\n\n"
            f"{self._plain_bullet_list(questions)}\n\n"
            "## 暴露问题\n\n"
            f"{self._plain_bullet_list(weak_points)}\n"
        )
        if current is None:
            self.artifact_service.create_markdown(
                relative_path,
                kind="high_frequency",
                body=body,
                language=config.language or "zh-CN",
                origin="llm",
                edited_by="system",
            )
            return
        self.artifact_service.replace_body(
            relative_path,
            expected_revision=current.front_matter.revision,
            body=body,
            edited_by="system",
            now=datetime.now(UTC),
        )

    def _replace_or_append_h2(self, body: str, heading: str, content: str) -> str:
        replacement = f"## {heading}\n\n{content.strip()}\n"
        pattern = re.compile(
            rf"^##[ \t]+{re.escape(heading)}[ \t]*\n.*?(?=^##[ \t]+|\Z)",
            flags=re.DOTALL | re.MULTILINE,
        )
        if pattern.search(body):
            return pattern.sub(replacement, body, count=1)
        return f"{body.rstrip()}\n\n{replacement}"

    def _markdown_sections(self, markdown: str) -> dict[str, str]:
        sections: dict[str, list[str]] = {}
        current: str | None = None
        for line in markdown.splitlines():
            heading = re.match(r"^##\s+(.+)$", line.strip())
            if heading:
                current = heading.group(1).strip().lower()
                sections.setdefault(current, [])
                continue
            if current:
                sections[current].append(line)
        return {heading: "\n".join(lines).strip() for heading, lines in sections.items()}

    def _markdown_list_items(self, markdown: str) -> list[str]:
        items: list[str] = []
        for line in markdown.splitlines():
            match = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", line)
            if match:
                items.append(match.group(1).strip())
        return self._top_items(items, 100)

    def _plain_bullet_list(self, items: list[str]) -> str:
        compact = self._top_items(items, 100)
        if not compact:
            return "- 暂无。"
        return "\n".join(f"- {item}" for item in compact)

    def _upsert_question_bank_entry(
        self,
        session: Session,
        config: InterviewConfig,
        turn: InterviewTurn,
        *,
        question: str,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        if self.artifact_service is None or self.workspace_service is None:
            return
        if not (
            evaluation.should_write_weakness
            or evaluation.missing_points
            or evaluation.weaknesses
        ):
            return

        relative_path = self._question_bank_path(question)
        body = self._question_bank_body(session, config, question, evaluation)
        existing = self.artifact_repository.get_by_relative_path(session, relative_path)
        path_exists = self.workspace_service.resolve_path(relative_path).exists()
        if existing is not None or path_exists:
            current = self.artifact_service.read_markdown(relative_path)
            self.artifact_service.replace_body(
                relative_path,
                expected_revision=current.front_matter.revision,
                body=body,
                edited_by="system",
            )
        else:
            self.artifact_service.create_markdown(
                relative_path,
                kind="question_bank",
                body=body,
                language=config.language,
                evidence_refs=[f"interview_turn:{turn.id}"],
                origin="llm",
                edited_by="system",
            )
        self.workspace_service.rebuild_projection(
            session,
            self.artifact_repository,
            self.artifact_service,
        )

    def _question_bank_path(self, question: str) -> str:
        digest = hashlib.sha1(question.strip().encode("utf-8")).hexdigest()[:10]
        words = re.findall(r"[A-Za-z0-9]+", question.lower())
        slug = "-".join(words)[:60].strip("-") or "question"
        return f"questions/{slug}-{digest}.md"

    def _question_bank_body(
        self,
        session: Session,
        config: InterviewConfig,
        question: str,
        evaluation: AnswerEvaluationResult,
    ) -> str:
        tested_points = evaluation.tested_points or [config.target_role, config.job_description]
        error_points = [
            *evaluation.missing_points,
            *evaluation.weaknesses,
        ]
        project_lines = [
            item.split("\n", 1)[1].strip()
            for item in self._project_context(session)
            if "\n" in item and item.split("\n", 1)[1].strip()
        ]
        project_section = (
            "\n\n".join(project_lines)
            if project_lines
            else "结合已有项目材料补充业务场景、角色职责、技术取舍和结果指标。"
        )
        review_status = evaluation.mastery_change or "weak"
        if evaluation.should_write_weakness:
            review_status = review_status if "weak" in review_status else f"{review_status}；写入薄弱点"
        if evaluation.should_write_high_frequency:
            review_status = f"{review_status}；写入高频题"

        return (
            f"## 问题：{question.strip()}\n\n"
            "### 考察点\n\n"
            f"{self._bullet_list(tested_points)}\n\n"
            "### 标准回答\n\n"
            f"{(evaluation.better_answer or evaluation.feedback).strip()}\n\n"
            "### 结合项目\n\n"
            f"{project_section}\n\n"
            "### 常见追问\n\n"
            f"{evaluation.follow_up_question.strip() or '暂无。'}\n\n"
            "### 易错点\n\n"
            f"{self._bullet_list(error_points or evaluation.review_suggestions)}\n\n"
            "### 复习状态\n\n"
            f"{review_status}\n"
        )

    def _top_items(self, values: list[str], limit: int) -> list[str]:
        seen: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen[:limit]

    def _bullet_list(self, items: list[str]) -> str:
        cleaned = [item.strip() for item in items if item.strip()]
        if not cleaned:
            return "- 暂无。"
        return "\n".join(f"- {item}" for item in cleaned)

    def _get_active_session(self, session: Session, session_id: str) -> InterviewSession:
        interview_session = self.session_repository.get(session, session_id)
        if interview_session is None:
            raise not_found("session_not_found", "Interview session not found.")
        if interview_session.status != "active":
            raise conflict("session_not_active", "Interview session is not active.")
        return interview_session

    def _current_turn(self, session: Session, interview_session: InterviewSession) -> InterviewTurn:
        turns = self.turn_repository.list_for_session(session, interview_session.id)
        for turn in reversed(turns):
            if turn.round_index == interview_session.current_round:
                return turn
        raise not_found("turn_not_found", "Current interview turn not found.")

    def finish_session(self, session: Session, session_id: str):
        return MemoryService().finish_session(session, session_id)

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
