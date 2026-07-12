from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models
from app.schemas.modeling import ModelRef


@dataclass(frozen=True, slots=True)
class ConversationRecentProjection:
    conversation: models.Conversation
    last_message: str | None


class ConversationRepository:
    def create_generating(
        self,
        session: Session,
        *,
        user_id: int,
        agent_id: str,
        title: str,
        model_override: ModelRef | None,
    ) -> models.Conversation:
        conversation = models.Conversation(
            user_id=user_id,
            agent_id=agent_id,
            title=title,
            status="generating",
            model_override_json=(
                model_override.model_dump(mode="json")
                if model_override is not None
                else None
            ),
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
    ) -> models.Conversation | None:
        return session.scalar(
            select(models.Conversation).where(
                models.Conversation.id == conversation_id,
                models.Conversation.user_id == user_id,
                models.Conversation.deleted_at.is_(None),
            )
        )

    def get_for_update(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
    ) -> models.Conversation | None:
        return session.scalar(
            select(models.Conversation)
            .where(
                models.Conversation.id == conversation_id,
                models.Conversation.user_id == user_id,
                models.Conversation.deleted_at.is_(None),
            )
            .with_for_update()
        )

    def list_recent(
        self,
        session: Session,
        *,
        user_id: int,
        limit: int = 50,
    ) -> list[ConversationRecentProjection]:
        content_without_common_whitespace = func.replace(
            func.replace(
                func.replace(
                    func.replace(models.Message.content, " ", ""),
                    "\t",
                    "",
                ),
                "\n",
                "",
            ),
            "\r",
            "",
        )
        last_message = (
            select(models.Message.content)
            .where(
                models.Message.user_id == models.Conversation.user_id,
                models.Message.conversation_id == models.Conversation.id,
                func.length(content_without_common_whitespace) > 0,
            )
            .order_by(models.Message.sequence.desc())
            .limit(1)
            .correlate(models.Conversation)
            .scalar_subquery()
        )
        rows = session.execute(
            select(models.Conversation, last_message.label("last_message"))
            .where(
                models.Conversation.user_id == user_id,
                models.Conversation.deleted_at.is_(None),
            )
            .order_by(
                models.Conversation.created_at.desc(),
                models.Conversation.id.desc(),
            )
            .limit(limit)
        )
        return [
            ConversationRecentProjection(
                conversation=conversation,
                last_message=message_content,
            )
            for conversation, message_content in rows
        ]

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

    def list_model_history(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
        max_messages: int = 200,
    ) -> list[models.Message]:
        if max_messages <= 0:
            return []
        messages = list(
            session.scalars(
                select(models.Message)
                .where(
                    models.Message.user_id == user_id,
                    models.Message.conversation_id == conversation_id,
                    models.Message.role.in_(["user", "assistant"]),
                    models.Message.status == "completed",
                )
                .order_by(models.Message.sequence.desc())
                .limit(max_messages)
            )
        )
        messages.reverse()
        return messages

    def append_pending_turn(
        self,
        session: Session,
        *,
        conversation: models.Conversation,
        text: str,
        provider: str,
        model: str,
        metadata: dict[str, object],
    ) -> tuple[models.Message, models.Message]:
        next_sequence = (
            session.scalar(
                select(func.max(models.Message.sequence)).where(
                    models.Message.user_id == conversation.user_id,
                    models.Message.conversation_id == conversation.id,
                )
            )
            or 0
        ) + 1
        user_message = models.Message(
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            sequence=next_sequence,
            role="user",
            status="completed",
            content=text,
            metadata_json={},
        )
        assistant = models.Message(
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            sequence=next_sequence + 1,
            role="assistant",
            status="pending",
            content="",
            provider=provider,
            model=model,
            metadata_json=metadata,
        )
        conversation.status = "generating"
        conversation.updated_at = models._now()
        session.add_all([user_message, assistant])
        session.flush()
        return user_message, assistant

    def checkpoint_assistant(
        self,
        session: Session,
        *,
        user_id: int,
        message_id: str,
        content: str,
        status: str = "streaming",
    ) -> models.Message:
        message = session.scalar(
            select(models.Message)
            .where(
                models.Message.id == message_id,
                models.Message.user_id == user_id,
                models.Message.role == "assistant",
            )
            .with_for_update()
        )
        if message is None:
            raise ValueError("assistant_message_not_found")
        if message.status not in {"pending", "streaming"}:
            raise ValueError("assistant_message_not_writable")
        message.content = content
        message.status = status
        message.updated_at = models._now()
        session.flush()
        return message

    def finish_assistant(
        self,
        session: Session,
        *,
        user_id: int,
        message_id: str,
        content: str,
        status: Literal["completed", "failed"],
        error_code: str | None = None,
    ) -> models.Message:
        message = self.checkpoint_assistant(
            session,
            user_id=user_id,
            message_id=message_id,
            content=content,
            status=status,
        )
        if error_code is not None:
            message.metadata_json = {
                **(message.metadata_json or {}),
                "error_code": error_code,
            }
        conversation = session.scalar(
            select(models.Conversation)
            .where(
                models.Conversation.id == message.conversation_id,
                models.Conversation.user_id == user_id,
            )
            .with_for_update()
        )
        if conversation is None:
            raise ValueError("conversation_not_found")
        conversation.status = "idle"
        conversation.updated_at = models._now()
        session.flush()
        return message

    def set_model_override(
        self,
        session: Session,
        *,
        conversation: models.Conversation,
        model_override: ModelRef | None,
    ) -> models.Conversation:
        conversation.model_override_json = (
            model_override.model_dump(mode="json")
            if model_override is not None
            else None
        )
        conversation.updated_at = models._now()
        session.flush()
        return conversation

    def rename(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
        title: str,
    ) -> models.Conversation | None:
        conversation = self.get_for_update(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            return None
        conversation.title = title
        conversation.updated_at = models._now()
        session.flush()
        return conversation

    def soft_delete(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str,
    ) -> bool:
        conversation = self.get_for_update(
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

    def recover_interrupted(self, session: Session) -> int:
        messages = list(
            session.scalars(
                select(models.Message)
                .where(
                    models.Message.role == "assistant",
                    models.Message.status.in_(["pending", "streaming"]),
                )
                .with_for_update()
            )
        )
        conversation_ids: set[str] = set()
        for message in messages:
            message.status = "failed"
            message.metadata_json = {
                **(message.metadata_json or {}),
                "error_code": "generation_interrupted",
            }
            message.updated_at = models._now()
            conversation_ids.add(message.conversation_id)
        if conversation_ids:
            conversations = session.scalars(
                select(models.Conversation)
                .where(models.Conversation.id.in_(conversation_ids))
                .with_for_update()
            )
            for conversation in conversations:
                conversation.status = "idle"
                conversation.updated_at = models._now()
        session.flush()
        return len(messages)
