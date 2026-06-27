from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db import models
from app.repositories.database import LearningMessageRepository, LearningSessionRepository
from app.schemas.modeling import LearningNoteSummaryResult


class LearningConversationService:
    def __init__(
        self,
        *,
        session_repository: LearningSessionRepository | None = None,
        message_repository: LearningMessageRepository | None = None,
    ) -> None:
        self.session_repository = session_repository or LearningSessionRepository()
        self.message_repository = message_repository or LearningMessageRepository()

    def get_or_create_session(
        self,
        session: Session,
        *,
        conversation_id: str | None,
        title: str,
        language: str,
        provider: str | None,
        model: str | None,
    ) -> models.LearningSession:
        if conversation_id:
            return self.require_session(session, conversation_id)
        return self.session_repository.add(
            session,
            models.LearningSession(
                title=self._title(title, language),
                language=language,
                chat_model_provider=provider or "",
                chat_model=model or "",
            ),
        )

    def require_session(
        self,
        session: Session,
        conversation_id: str,
    ) -> models.LearningSession:
        learning_session = self.session_repository.get(session, conversation_id)
        if learning_session is None:
            raise not_found("learning_session_not_found", "Learning conversation not found.")
        return learning_session

    def append_note_exchange(
        self,
        session: Session,
        learning_session: models.LearningSession,
        *,
        note: str,
        assistant_markdown: str,
        summary: LearningNoteSummaryResult,
        source_artifact_id: str,
        source_relative_path: str,
        artifact_id: str,
        artifact_path: str,
    ) -> None:
        now = datetime.now(UTC)
        self.message_repository.add(
            session,
            models.LearningMessage(
                session_id=learning_session.id,
                role="user",
                message_type="learning_input",
                content=note,
                artifact_id=source_artifact_id,
                artifact_path=source_relative_path,
                message_metadata={
                    "source_artifact_id": source_artifact_id,
                    "source_relative_path": source_relative_path,
                },
            ),
        )
        self.message_repository.add(
            session,
            models.LearningMessage(
                session_id=learning_session.id,
                role="assistant",
                message_type="learning_summary",
                content=assistant_markdown,
                artifact_id=artifact_id,
                artifact_path=artifact_path,
                message_metadata={
                    "artifact_id": artifact_id,
                    "artifact_path": artifact_path,
                    "summary": summary.model_dump(mode="json"),
                },
            ),
        )
        learning_session.title = self._title(summary.title, learning_session.language)
        learning_session.updated_at = now
        session.flush()

    @staticmethod
    def _title(value: str, language: str) -> str:
        fallback = "学习记录" if language == "zh-CN" else "Learning note"
        return (value.strip() or fallback)[:255]
