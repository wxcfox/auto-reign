from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import models
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.conversations import (
    ConversationDetailResponse,
    ConversationHistoryItemResponse,
    ConversationMessageResponse,
)


class ConversationService:
    def __init__(self, repository: ConversationRepository | None = None) -> None:
        self.repository = repository or ConversationRepository()

    def list_conversations(
        self,
        session: Session,
        *,
        user_id: int,
        limit: int = 50,
    ) -> list[ConversationHistoryItemResponse]:
        conversations = self.repository.list_recent(
            session,
            user_id=user_id,
            limit=limit,
        )
        return [
            self._history_item(
                conversation,
                self.repository.list_messages(
                    session,
                    user_id=user_id,
                    conversation_id=conversation.id,
                ),
            )
            for conversation in conversations
        ]

    def get_conversation(
        self,
        session: Session,
        conversation_id: str,
        *,
        user_id: int,
    ) -> ConversationDetailResponse | None:
        conversation = self.repository.get(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return None
        messages = self.repository.list_messages(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
        )
        return ConversationDetailResponse(
            **self._history_item(conversation, messages).model_dump(),
            messages=[self._message_response(message) for message in messages],
        )

    def rename_conversation(
        self,
        session: Session,
        conversation_id: str,
        title: str,
        *,
        user_id: int,
    ) -> ConversationHistoryItemResponse | None:
        conversation = self.repository.rename(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            title=title,
        )
        if conversation is None:
            return None
        messages = self.repository.list_messages(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
        )
        return self._history_item(conversation, messages)

    def delete_conversation(
        self,
        session: Session,
        conversation_id: str,
        *,
        user_id: int,
    ) -> bool:
        return self.repository.soft_delete(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    def _history_item(
        self,
        conversation: models.Conversation,
        messages: list[models.Message],
    ) -> ConversationHistoryItemResponse:
        return ConversationHistoryItemResponse(
            id=conversation.id,
            kind=conversation.kind,
            title=self._title(conversation),
            href=self._href(conversation),
            started_at=conversation.created_at,
            updated_at=conversation.updated_at,
            last_message=_excerpt(self._last_message(conversation, messages)),
        )

    @staticmethod
    def _message_response(message: models.Message) -> ConversationMessageResponse:
        return ConversationMessageResponse(
            id=message.id,
            role=message.role,
            message_type=message.message_type,
            content=message.content,
            created_at=message.created_at,
            metadata=message.metadata_json,
        )

    @staticmethod
    def _title(conversation: models.Conversation) -> str:
        if conversation.title.strip():
            return conversation.title.strip()
        if conversation.kind == "interview":
            return "未命名面试"
        return "学习记录"

    @staticmethod
    def _href(conversation: models.Conversation) -> str:
        if conversation.kind == "interview":
            return f"/interview?session={conversation.id}"
        return f"/learn?session={conversation.id}"

    @staticmethod
    def _last_message(
        conversation: models.Conversation,
        messages: list[models.Message],
    ) -> str:
        summary = conversation.summary_json or {}
        last_message = summary.get("last_message")
        if isinstance(last_message, str) and last_message.strip():
            return last_message
        if messages:
            return messages[-1].content
        return conversation.title


def _excerpt(value: str, max_length: int = 160) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1]}..."
