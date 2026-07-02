from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.db import models
from app.repositories.database import (
    InterviewSessionRepository,
    InterviewTurnRepository,
    LearningMessageRepository,
    LearningSessionRepository,
)
from app.schemas.conversations import (
    ConversationDetailResponse,
    ConversationHistoryItemResponse,
    ConversationMessageResponse,
)


class ConversationAdapter(Protocol):
    def list_recent(
        self,
        session: Session,
        *,
        limit: int,
    ) -> list[ConversationHistoryItemResponse]: ...

    def get_detail(
        self,
        session: Session,
        conversation_id: str,
    ) -> ConversationDetailResponse | None: ...

    def rename(
        self,
        session: Session,
        conversation_id: str,
        title: str,
    ) -> ConversationHistoryItemResponse | None: ...

    def delete(
        self,
        session: Session,
        conversation_id: str,
    ) -> bool: ...


class ConversationService:
    def __init__(self, adapters: list[ConversationAdapter] | None = None) -> None:
        self.adapters = adapters or [
            InterviewConversationAdapter(),
            LearningConversationAdapter(),
        ]

    def list_conversations(
        self,
        session: Session,
        *,
        limit: int = 50,
    ) -> list[ConversationHistoryItemResponse]:
        conversations = [
            conversation
            for adapter in self.adapters
            for conversation in adapter.list_recent(session, limit=limit)
        ]
        return sorted(conversations, key=lambda item: item.started_at, reverse=True)[:limit]

    def get_conversation(
        self,
        session: Session,
        conversation_id: str,
    ) -> ConversationDetailResponse | None:
        for adapter in self.adapters:
            detail = adapter.get_detail(session, conversation_id)
            if detail is not None:
                return detail
        return None

    def rename_conversation(
        self,
        session: Session,
        conversation_id: str,
        title: str,
    ) -> ConversationHistoryItemResponse | None:
        for adapter in self.adapters:
            renamed = adapter.rename(session, conversation_id, title)
            if renamed is not None:
                return renamed
        return None

    def delete_conversation(
        self,
        session: Session,
        conversation_id: str,
    ) -> bool:
        for adapter in self.adapters:
            if adapter.delete(session, conversation_id):
                return True
        return False


class LearningConversationAdapter:
    def __init__(
        self,
        *,
        session_repository: LearningSessionRepository | None = None,
        message_repository: LearningMessageRepository | None = None,
    ) -> None:
        self.session_repository = session_repository or LearningSessionRepository()
        self.message_repository = message_repository or LearningMessageRepository()

    def list_recent(
        self,
        session: Session,
        *,
        limit: int,
    ) -> list[ConversationHistoryItemResponse]:
        conversations: list[ConversationHistoryItemResponse] = []
        for learning_session in self.session_repository.list_recent(session, limit=limit):
            messages = self.message_repository.list_for_session(session, learning_session.id)
            conversations.append(self._history_item(learning_session, messages))
        return conversations

    def get_detail(
        self,
        session: Session,
        conversation_id: str,
    ) -> ConversationDetailResponse | None:
        learning_session = self.session_repository.get(session, conversation_id)
        if learning_session is None:
            return None
        messages = self.message_repository.list_for_session(session, learning_session.id)
        return ConversationDetailResponse(
            **self._history_item(learning_session, messages).model_dump(),
            messages=[self._message_response(message) for message in messages],
        )

    def rename(
        self,
        session: Session,
        conversation_id: str,
        title: str,
    ) -> ConversationHistoryItemResponse | None:
        learning_session = self.session_repository.get(session, conversation_id)
        if learning_session is None:
            return None
        learning_session.title = title
        learning_session.updated_at = _now()
        session.flush()
        messages = self.message_repository.list_for_session(session, learning_session.id)
        return self._history_item(learning_session, messages)

    def delete(
        self,
        session: Session,
        conversation_id: str,
    ) -> bool:
        learning_session = self.session_repository.get(session, conversation_id)
        if learning_session is None:
            return False
        deleted_at = _now()
        learning_session.deleted_at = deleted_at
        learning_session.updated_at = deleted_at
        session.flush()
        return True

    def _history_item(
        self,
        learning_session: models.LearningSession,
        messages: list[models.LearningMessage],
    ) -> ConversationHistoryItemResponse:
        last_message = messages[-1].content if messages else learning_session.title
        return ConversationHistoryItemResponse(
            id=learning_session.id,
            kind="learning",
            title=learning_session.title,
            href=f"/learn?session={learning_session.id}",
            started_at=learning_session.started_at,
            updated_at=learning_session.updated_at,
            last_message=_excerpt(last_message),
        )

    @staticmethod
    def _message_response(message: models.LearningMessage) -> ConversationMessageResponse:
        return ConversationMessageResponse(
            id=message.id,
            role=message.role,
            message_type=message.message_type,
            content=message.content,
            created_at=message.created_at,
            metadata=message.message_metadata,
        )


class InterviewConversationAdapter:
    def __init__(
        self,
        *,
        session_repository: InterviewSessionRepository | None = None,
        turn_repository: InterviewTurnRepository | None = None,
    ) -> None:
        self.session_repository = session_repository or InterviewSessionRepository()
        self.turn_repository = turn_repository or InterviewTurnRepository()

    def list_recent(
        self,
        session: Session,
        *,
        limit: int,
    ) -> list[ConversationHistoryItemResponse]:
        conversations: list[ConversationHistoryItemResponse] = []
        for interview_session in self.session_repository.list_recent(session, limit=limit):
            turns = self.turn_repository.list_for_session(session, interview_session.id)
            conversations.append(self._history_item(interview_session, turns))
        return conversations

    def get_detail(
        self,
        session: Session,
        conversation_id: str,
    ) -> ConversationDetailResponse | None:
        interview_session = self.session_repository.get(session, conversation_id)
        if interview_session is None:
            return None
        turns = self.turn_repository.list_for_session(session, conversation_id)
        return ConversationDetailResponse(
            **self._history_item(interview_session, turns).model_dump(),
            messages=self._messages(turns),
        )

    def rename(
        self,
        session: Session,
        conversation_id: str,
        title: str,
    ) -> ConversationHistoryItemResponse | None:
        interview_session = self.session_repository.get(session, conversation_id)
        if interview_session is None:
            return None
        interview_session.title = title
        session.flush()
        turns = self.turn_repository.list_for_session(session, interview_session.id)
        return self._history_item(interview_session, turns)

    def delete(
        self,
        session: Session,
        conversation_id: str,
    ) -> bool:
        interview_session = self.session_repository.get(session, conversation_id)
        if interview_session is None:
            return False
        interview_session.deleted_at = _now()
        session.flush()
        return True

    def _history_item(
        self,
        interview_session: models.InterviewSession,
        turns: list[models.InterviewTurn],
    ) -> ConversationHistoryItemResponse:
        return ConversationHistoryItemResponse(
            id=interview_session.id,
            kind="interview",
            title=self._title(interview_session),
            href=f"/interview?session={interview_session.id}",
            started_at=interview_session.started_at,
            updated_at=self._updated_at(interview_session, turns),
            last_message=_excerpt(self._last_message(turns)),
        )

    @staticmethod
    def _title(interview_session: models.InterviewSession) -> str:
        if interview_session.title and interview_session.title.strip():
            return interview_session.title.strip()
        config = interview_session.config
        natural_context = config.extra_prompt.strip()
        if natural_context:
            return natural_context
        structured = " ".join(
            item.strip()
            for item in [config.target_company, config.target_role]
            if item.strip()
        )
        fallback = "未命名面试" if config.language == "zh-CN" else "Untitled interview"
        return structured or fallback

    @staticmethod
    def _updated_at(
        interview_session: models.InterviewSession,
        turns: list[models.InterviewTurn],
    ) -> datetime:
        dates = [interview_session.started_at]
        if interview_session.ended_at is not None:
            dates.append(interview_session.ended_at)
        dates.extend(turn.created_at for turn in turns)
        return max(dates)

    @staticmethod
    def _last_message(turns: list[models.InterviewTurn]) -> str:
        if not turns:
            return ""
        turn = turns[-1]
        for value in [
            turn.follow_up_feedback,
            turn.follow_up_answer,
            turn.follow_up_question,
            turn.feedback,
            turn.answer,
            turn.question,
        ]:
            if value:
                return value
        return ""

    @staticmethod
    def _messages(turns: list[models.InterviewTurn]) -> list[ConversationMessageResponse]:
        messages: list[ConversationMessageResponse] = []
        for turn in turns:
            messages.append(
                ConversationMessageResponse(
                    id=f"{turn.id}:question",
                    role="assistant",
                    message_type="interview_question",
                    content=turn.question,
                    created_at=turn.created_at,
                    metadata={"turn_id": turn.id, "round_index": turn.round_index},
                )
            )
            if turn.answer:
                messages.append(
                    ConversationMessageResponse(
                        id=f"{turn.id}:answer",
                        role="user",
                        message_type="interview_answer",
                        content=turn.answer,
                        created_at=turn.created_at,
                        metadata={"turn_id": turn.id, "round_index": turn.round_index},
                    )
                )
            if turn.feedback:
                messages.append(
                    ConversationMessageResponse(
                        id=f"{turn.id}:feedback",
                        role="assistant",
                        message_type="interview_feedback",
                        content=turn.feedback,
                        created_at=turn.created_at,
                        metadata={"turn_id": turn.id, "round_index": turn.round_index},
                    )
                )
            if turn.follow_up_question:
                messages.append(
                    ConversationMessageResponse(
                        id=f"{turn.id}:follow-up-question",
                        role="assistant",
                        message_type="interview_follow_up_question",
                        content=turn.follow_up_question,
                        created_at=turn.created_at,
                        metadata={"turn_id": turn.id, "round_index": turn.round_index},
                    )
                )
            if turn.follow_up_answer:
                messages.append(
                    ConversationMessageResponse(
                        id=f"{turn.id}:follow-up-answer",
                        role="user",
                        message_type="interview_follow_up_answer",
                        content=turn.follow_up_answer,
                        created_at=turn.created_at,
                        metadata={"turn_id": turn.id, "round_index": turn.round_index},
                    )
                )
            if turn.follow_up_feedback:
                messages.append(
                    ConversationMessageResponse(
                        id=f"{turn.id}:follow-up-feedback",
                        role="assistant",
                        message_type="interview_follow_up_feedback",
                        content=turn.follow_up_feedback,
                        created_at=turn.created_at,
                        metadata={"turn_id": turn.id, "round_index": turn.round_index},
                    )
                )
        return messages


def _excerpt(value: str, max_length: int = 160) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1]}..."


def _now() -> datetime:
    return datetime.now(UTC)
