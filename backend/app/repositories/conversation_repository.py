from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models


class ConversationRepository:
    def create(
        self,
        session: Session,
        *,
        user_id: int,
        kind: str,
        title: str,
        status: str = "active",
        config_json: dict[str, object] | None = None,
        summary_json: dict[str, object] | None = None,
    ) -> models.Conversation:
        conversation = models.Conversation(
            user_id=user_id,
            kind=kind,
            title=title,
            status=status,
            config_json=config_json or {},
            summary_json=summary_json or {},
        )
        session.add(conversation)
        session.flush()
        return conversation

    def get(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
        kind: str | None = None,
    ) -> models.Conversation | None:
        filters = [
            models.Conversation.user_id == user_id,
            models.Conversation.id == conversation_id,
            models.Conversation.deleted_at.is_(None),
        ]
        if kind is not None:
            filters.append(models.Conversation.kind == kind)
        return session.scalar(select(models.Conversation).where(*filters))

    def list_recent(
        self,
        session: Session,
        *,
        user_id: int,
        limit: int = 50,
    ) -> list[models.Conversation]:
        return list(
            session.scalars(
                select(models.Conversation)
                .where(
                    models.Conversation.user_id == user_id,
                    models.Conversation.deleted_at.is_(None),
                )
                .order_by(models.Conversation.created_at.desc())
                .limit(limit)
            )
        )

    def add_message(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
        role: str,
        message_type: str,
        content: str,
        metadata_json: dict[str, object] | None = None,
    ) -> models.Message:
        conversation = self.get(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise ValueError("conversation_not_found")
        next_sequence = (
            session.scalar(
                select(func.max(models.Message.sequence)).where(
                    models.Message.user_id == user_id,
                    models.Message.conversation_id == conversation_id,
                )
            )
            or 0
        ) + 1
        message = models.Message(
            user_id=user_id,
            conversation_id=conversation_id,
            sequence=next_sequence,
            role=role,
            message_type=message_type,
            content=content,
            metadata_json=metadata_json or {},
        )
        session.add(message)
        conversation.updated_at = models._now()
        session.flush()
        return message

    def list_messages(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
    ) -> list[models.Message]:
        return list(
            session.scalars(
                select(models.Message)
                .where(
                    models.Message.user_id == user_id,
                    models.Message.conversation_id == conversation_id,
                )
                .order_by(models.Message.sequence)
            )
        )

    def rename(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
        title: str,
    ) -> models.Conversation | None:
        conversation = self.get(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return None
        conversation.title = title
        session.flush()
        return conversation

    def soft_delete(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
    ) -> bool:
        conversation = self.get(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return False
        deleted_at = models._now()
        conversation.deleted_at = deleted_at
        conversation.updated_at = deleted_at
        session.flush()
        return True

    def update_summary(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
        summary_json: dict[str, object],
        updated_at: datetime | None = None,
    ) -> models.Conversation | None:
        conversation = self.get(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return None
        conversation.summary_json = summary_json
        if updated_at is not None:
            conversation.updated_at = updated_at
        session.flush()
        return conversation
