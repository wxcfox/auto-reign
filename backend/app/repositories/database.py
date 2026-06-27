from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


class InterviewConfigRepository:
    def get_last(self, session: Session) -> models.InterviewConfig | None:
        return session.scalar(
            select(models.InterviewConfig).where(models.InterviewConfig.is_last_used.is_(True))
        )

    def add(self, session: Session, config: models.InterviewConfig) -> models.InterviewConfig:
        session.add(config)
        session.flush()
        return config

    def clear_last_used(self, session: Session) -> None:
        for config in session.scalars(
            select(models.InterviewConfig).where(models.InterviewConfig.is_last_used.is_(True))
        ):
            config.is_last_used = False
        session.flush()


class InterviewSessionRepository:
    def add(self, session: Session, interview_session: models.InterviewSession) -> models.InterviewSession:
        session.add(interview_session)
        session.flush()
        return interview_session

    def get(self, session: Session, session_id: str) -> models.InterviewSession | None:
        return session.get(models.InterviewSession, session_id)

    def list_recent(self, session: Session, limit: int = 50) -> list[models.InterviewSession]:
        return list(
            session.scalars(
                select(models.InterviewSession)
                .order_by(models.InterviewSession.started_at.desc())
                .limit(limit)
            )
        )


class InterviewTurnRepository:
    def add(self, session: Session, turn: models.InterviewTurn) -> models.InterviewTurn:
        session.add(turn)
        session.flush()
        return turn

    def list_for_session(self, session: Session, session_id: str) -> list[models.InterviewTurn]:
        return list(
            session.scalars(
                select(models.InterviewTurn)
                .where(models.InterviewTurn.session_id == session_id)
                .order_by(models.InterviewTurn.round_index)
            )
        )


class ReportRepository:
    def add(self, session: Session, report: models.Report) -> models.Report:
        session.add(report)
        session.flush()
        return report

    def get(self, session: Session, report_id: str) -> models.Report | None:
        return session.get(models.Report, report_id)

    def list(self, session: Session) -> list[models.Report]:
        return list(session.scalars(select(models.Report).order_by(models.Report.created_at.desc())))


class LearningSessionRepository:
    def add(
        self,
        session: Session,
        learning_session: models.LearningSession,
    ) -> models.LearningSession:
        session.add(learning_session)
        session.flush()
        return learning_session

    def get(self, session: Session, session_id: str) -> models.LearningSession | None:
        return session.get(models.LearningSession, session_id)

    def list_recent(self, session: Session, limit: int = 50) -> list[models.LearningSession]:
        return list(
            session.scalars(
                select(models.LearningSession)
                .order_by(models.LearningSession.updated_at.desc())
                .limit(limit)
            )
        )


class LearningMessageRepository:
    def add(self, session: Session, message: models.LearningMessage) -> models.LearningMessage:
        session.add(message)
        session.flush()
        return message

    def list_for_session(self, session: Session, session_id: str) -> list[models.LearningMessage]:
        return list(
            session.scalars(
                select(models.LearningMessage)
                .where(models.LearningMessage.session_id == session_id)
                .order_by(models.LearningMessage.created_at)
            )
        )
