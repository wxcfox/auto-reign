import re
from collections.abc import Iterator
from dataclasses import dataclass
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
from app.schemas.modeling import (
    AnswerEvaluationRequest,
    AnswerEvaluationResult,
    QuestionGenerationRequest,
)
from app.services.model_service import ModelService
from app.services.artifact_service import ArtifactService
from app.services.context_assembler import ContextAssembler
from app.services.interview_completion_service import InterviewCompletionService
from app.services.interview_artifact_service import InterviewArtifactService
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
        interview_artifact_service: InterviewArtifactService | None = None,
        interview_completion_service: InterviewCompletionService | None = None,
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
        self.interview_artifact_service = interview_artifact_service
        self.interview_completion_service = interview_completion_service

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
        self._apply_main_evaluation(turn, answer, evaluation)
        self._record_answer_evaluation(session, config, turn, turn.question, evaluation)
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
            self._apply_main_evaluation(turn, answer, evaluation)
            self._record_answer_evaluation(session, config, turn, turn.question, evaluation)
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
        self._apply_follow_up_evaluation(turn, answer, evaluation)
        self._record_answer_evaluation(session, config, turn, turn.follow_up_question, evaluation)
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
            self._apply_follow_up_evaluation(turn, answer, evaluation)
            self._record_answer_evaluation(session, config, turn, turn.follow_up_question, evaluation)
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

    def _apply_main_evaluation(
        self,
        turn: InterviewTurn,
        answer: str,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        turn.answer = answer
        turn.feedback = evaluation.feedback
        turn.missing_points = evaluation.missing_points
        turn.follow_up_question = evaluation.follow_up_question
        turn.weaknesses = evaluation.weaknesses
        turn.review_suggestions = evaluation.review_suggestions
        turn.better_answer = evaluation.better_answer
        turn.mastery_change = evaluation.mastery_change
        turn.should_write_weakness = evaluation.should_write_weakness
        turn.should_write_high_frequency = evaluation.should_write_high_frequency
        turn.tested_points = evaluation.tested_points

    def _apply_follow_up_evaluation(
        self,
        turn: InterviewTurn,
        answer: str,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        turn.follow_up_answer = answer
        turn.follow_up_feedback = evaluation.feedback
        turn.follow_up_missing_points = evaluation.missing_points
        turn.follow_up_weaknesses = evaluation.weaknesses
        turn.follow_up_review_suggestions = evaluation.review_suggestions
        turn.follow_up_better_answer = evaluation.better_answer
        turn.follow_up_mastery_change = evaluation.mastery_change
        turn.follow_up_should_write_weakness = evaluation.should_write_weakness
        turn.follow_up_should_write_high_frequency = evaluation.should_write_high_frequency
        turn.follow_up_tested_points = evaluation.tested_points

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
        writer = self._artifact_writer()
        if writer is None:
            return
        turns = self.turn_repository.list_for_session(session, interview_session.id)
        writer.record_answer_progress(session, interview_session, config, turns)

    def _record_answer_evaluation(
        self,
        session: Session,
        config: InterviewConfig,
        turn: InterviewTurn,
        question: str,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        writer = self._artifact_writer()
        if writer is None:
            return
        writer.record_answer_evaluation(session, config, turn, question=question, evaluation=evaluation)

    def _artifact_writer(self) -> InterviewArtifactService | None:
        if self.interview_artifact_service is not None:
            return self.interview_artifact_service
        if self.artifact_service is None or self.workspace_service is None:
            return
        self.interview_artifact_service = InterviewArtifactService(
            workspace_service=self.workspace_service,
            artifact_service=self.artifact_service,
            artifact_repository=self.artifact_repository,
        )
        return self.interview_artifact_service

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
        service = self.interview_completion_service or InterviewCompletionService(
            model_service=self.model_service,
            interview_artifact_service=self._artifact_writer(),
        )
        return service.finish_session(session, session_id)

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
