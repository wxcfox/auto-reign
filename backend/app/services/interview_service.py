import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import bad_gateway, conflict, not_found
from app.db.models import InterviewConfig, InterviewSession, InterviewTurn
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
from app.services.memory_service import MemoryService
from app.services.rag_service import RagService
from app.services.workspace_retrieval_service import WorkspaceRetrievalService


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


class InterviewService:
    def __init__(
        self,
        config_repository: InterviewConfigRepository | None = None,
        session_repository: InterviewSessionRepository | None = None,
        turn_repository: InterviewTurnRepository | None = None,
        model_service: ModelService | None = None,
        rag_service: RagService | None = None,
        retrieval_service: WorkspaceRetrievalService | None = None,
    ) -> None:
        self.config_repository = config_repository or InterviewConfigRepository()
        self.session_repository = session_repository or InterviewSessionRepository()
        self.turn_repository = turn_repository or InterviewTurnRepository()
        self.model_service = model_service or ModelService()
        self.rag_service = rag_service or RagService()
        self.retrieval_service = retrieval_service or WorkspaceRetrievalService()

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

    def create_session(
        self, session: Session, config_in: InterviewConfigIn
    ) -> tuple[InterviewSession, InterviewTurn]:
        config = InterviewConfig(**config_in.model_dump(), is_last_used=False)
        self.config_repository.add(session, config)
        context_hits = self.retrieval_service.search(
            session,
            _target_context_query(
                target_company=config_in.target_company,
                target_role=config_in.target_role,
                job_description=config_in.job_description,
                extra_prompt=config_in.extra_prompt,
            ),
            limit=4,
        )
        context = [str(hit["content"]) for hit in context_hits]
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
        context_hits = self.retrieval_service.search(
            session,
            _target_context_query(
                target_company=config_in.target_company,
                target_role=config_in.target_role,
                job_description=config_in.job_description,
                extra_prompt=config_in.extra_prompt,
            ),
            limit=4,
        )
        request = QuestionGenerationRequest(
            target_company=config_in.target_company,
            target_role=config_in.target_role,
            job_description=config_in.job_description,
            extra_prompt=config_in.extra_prompt,
            language=config_in.language,
            mode=config_in.mode,
            context=[str(hit["content"]) for hit in context_hits],
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
        evaluation = self.model_service.evaluate_answer(
            AnswerEvaluationRequest(
                question=turn.question,
                answer=answer,
                language=config.language,
                context=[],
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
        session.flush()
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
        request = AnswerEvaluationRequest(
            question=turn.question,
            answer=answer,
            language=config.language,
            context=[],
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
            session.flush()
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
        evaluation = self.model_service.evaluate_answer(
            AnswerEvaluationRequest(
                question=turn.follow_up_question,
                answer=answer,
                language=config.language,
                context=[],
                provider=config.chat_model_provider,
                model=config.chat_model,
            )
        )
        turn.follow_up_answer = answer
        turn.follow_up_feedback = evaluation.feedback
        turn.follow_up_missing_points = evaluation.missing_points
        turn.follow_up_weaknesses = evaluation.weaknesses
        turn.follow_up_review_suggestions = evaluation.review_suggestions
        session.flush()
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
        request = AnswerEvaluationRequest(
            question=turn.follow_up_question,
            answer=answer,
            language=config.language,
            context=[],
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
            session.flush()
            yield InterviewStreamEvent(
                event="result",
                data={
                    "feedback": evaluation.feedback,
                    "missing_points": evaluation.missing_points,
                    "weaknesses": evaluation.weaknesses,
                    "review_suggestions": evaluation.review_suggestions,
                },
            )

        return events()

    def next_question(
        self, session: Session, session_id: str
    ) -> tuple[InterviewSession, InterviewTurn]:
        interview_session = self._get_active_session(session, session_id)
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        current_turn = self._current_turn(session, interview_session)
        if current_turn.answer is None:
            raise conflict("current_turn_unanswered", "Answer the current question before continuing.")
        if interview_session.current_round >= config.target_rounds:
            raise conflict("target_rounds_reached", "The configured target round count was reached.")
        next_round = interview_session.current_round + 1
        context_hits = self.retrieval_service.search(
            session,
            _target_context_query(
                target_company=config.target_company,
                target_role=config.target_role,
                job_description=config.job_description,
                extra_prompt=config.extra_prompt,
                round_index=next_round,
            ),
            limit=4,
        )
        question = self.model_service.generate_question(
            QuestionGenerationRequest(
                target_company=config.target_company,
                target_role=config.target_role,
                job_description=config.job_description,
                extra_prompt=config.extra_prompt,
                language=config.language,
                mode=config.mode,
                context=[str(hit["content"]) for hit in context_hits],
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
        self, session: Session, session_id: str
    ) -> Iterator[InterviewStreamEvent]:
        interview_session = self._get_active_session(session, session_id)
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        current_turn = self._current_turn(session, interview_session)
        if current_turn.answer is None:
            raise conflict("current_turn_unanswered", "Answer the current question before continuing.")
        if interview_session.current_round >= config.target_rounds:
            raise conflict("target_rounds_reached", "The configured target round count was reached.")
        next_round = interview_session.current_round + 1
        context_hits = self.retrieval_service.search(
            session,
            _target_context_query(
                target_company=config.target_company,
                target_role=config.target_role,
                job_description=config.job_description,
                extra_prompt=config.extra_prompt,
                round_index=next_round,
            ),
            limit=4,
        )
        request = QuestionGenerationRequest(
            target_company=config.target_company,
            target_role=config.target_role,
            job_description=config.job_description,
            extra_prompt=config.extra_prompt,
            language=config.language,
            mode=config.mode,
            context=[str(hit["content"]) for hit in context_hits],
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
        if evaluation.follow_up_question:
            sections.append("Follow-up\n" + evaluation.follow_up_question.strip())
        return "\n\n".join(section for section in sections if section)

    def _preview_delta(self, previous: str, current: str) -> str:
        if not current or current == previous:
            return ""
        if current.startswith(previous):
            return current[len(previous) :]
        return current

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
