from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import conflict, not_found
from app.db.models import InterviewConfig, InterviewSession, InterviewTurn, Report
from app.repositories.database import (
    InterviewSessionRepository,
    InterviewTurnRepository,
    ReportRepository,
)
from app.services.interview_artifact_service import InterviewArtifactService
from app.services.model_service import ModelService, ReportGenerationRequest


class InterviewCompletionService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        model_service: ModelService | None = None,
        interview_artifact_service: InterviewArtifactService | None = None,
        session_repository: InterviewSessionRepository | None = None,
        turn_repository: InterviewTurnRepository | None = None,
        report_repository: ReportRepository | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.model_service = model_service or ModelService()
        self.interview_artifact_service = interview_artifact_service or InterviewArtifactService(
            settings=self.settings
        )
        self.session_repository = session_repository or InterviewSessionRepository()
        self.turn_repository = turn_repository or InterviewTurnRepository()
        self.report_repository = report_repository or ReportRepository()

    def finish_session(self, session: Session, interview_session_id: str) -> tuple[InterviewSession, Report]:
        interview_session = self.session_repository.get(session, interview_session_id)
        if interview_session is None:
            raise not_found("session_not_found", "Interview session not found.")
        if interview_session.status != "active":
            raise conflict("session_not_active", "Interview session is not active.")
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")

        turns = self.turn_repository.list_for_session(session, interview_session.id)
        report_markdown = self.model_service.generate_report(
            ReportGenerationRequest(
                session_id=interview_session.id,
                language=config.language,
                turns=[
                    {
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
                    for turn in turns
                ],
                provider=config.chat_model_provider,
                model=config.chat_model,
            )
        )
        artifacts = self.interview_artifact_service.archive_finished_session(
            session,
            interview_session,
            config,
            turns,
            report_markdown,
        )
        report = self.report_repository.add(
            session,
            Report(
                session_id=interview_session.id,
                report_path=artifacts.report_path,
                summary=self.report_summary(
                    config.target_role,
                    config.target_company,
                    config.extra_prompt,
                    config.language,
                ),
                weaknesses=self.report_weaknesses(turns),
            ),
        )
        interview_session.status = "completed"
        interview_session.ended_at = datetime.now(UTC)
        interview_session.report_path = artifacts.report_path
        session.flush()
        return interview_session, report

    @staticmethod
    def report_weaknesses(turns: list[InterviewTurn]) -> list[str]:
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
