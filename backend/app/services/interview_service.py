from sqlalchemy.orm import Session

from app.core.errors import conflict, not_found
from app.db.models import InterviewConfig, InterviewSession, InterviewTurn
from app.repositories.sqlite import (
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


class InterviewService:
    def __init__(
        self,
        config_repository: InterviewConfigRepository | None = None,
        session_repository: InterviewSessionRepository | None = None,
        turn_repository: InterviewTurnRepository | None = None,
        model_service: ModelService | None = None,
        rag_service: RagService | None = None,
    ) -> None:
        self.config_repository = config_repository or InterviewConfigRepository()
        self.session_repository = session_repository or InterviewSessionRepository()
        self.turn_repository = turn_repository or InterviewTurnRepository()
        self.model_service = model_service or ModelService()
        self.rag_service = rag_service or RagService()

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
                mode="comprehensive",
                chat_model_provider="openai",
                chat_model="gpt-4.1-mini",
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
        context_hits = self.rag_service.search(
            session,
            f"{config_in.target_role} {config_in.job_description}",
            limit=4,
        )
        context = [str(hit["content"]) for hit in context_hits]
        question = self.model_service.generate_question(
            QuestionGenerationRequest(
                target_company=config_in.target_company,
                target_role=config_in.target_role,
                job_description=config_in.job_description,
                extra_prompt=config_in.extra_prompt,
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
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        evaluation = self.model_service.evaluate_answer(
            AnswerEvaluationRequest(
                question=turn.question,
                answer=answer,
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

    def submit_follow_up_answer(
        self, session: Session, session_id: str, answer: str
    ) -> InterviewTurn:
        interview_session = self._get_active_session(session, session_id)
        turn = self._current_turn(session, interview_session)
        turn.follow_up_answer = answer
        session.flush()
        return turn

    def next_question(
        self, session: Session, session_id: str
    ) -> tuple[InterviewSession, InterviewTurn]:
        interview_session = self._get_active_session(session, session_id)
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")
        next_round = interview_session.current_round + 1
        context_hits = self.rag_service.search(
            session,
            f"{config.target_role} {config.job_description} round {next_round}",
            limit=4,
        )
        question = self.model_service.generate_question(
            QuestionGenerationRequest(
                target_company=config.target_company,
                target_role=config.target_role,
                job_description=config.job_description,
                extra_prompt=config.extra_prompt,
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
