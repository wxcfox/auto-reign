from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db import models
from app.repositories.attachment_repository import AttachmentRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.resource_repository import ResourceRepository
from app.schemas.attachments import AttachmentResponse
from app.schemas.conversations import (
    ConversationAgentResponse,
    ConversationDetailResponse,
    ConversationHistoryItemResponse,
    ConversationMessageResponse,
)
from app.schemas.modeling import ModelRef


def conversation_message_response(
    message: models.Message,
    attachments: Sequence[models.Attachment] = (),
) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        id=message.id,
        role=message.role,
        status=message.status,
        content=message.content,
        provider=message.provider,
        model=message.model,
        created_at=message.created_at,
        updated_at=message.updated_at,
        metadata=message.metadata_json or {},
        attachments=[
            AttachmentResponse(
                id=attachment.id,
                filename=attachment.original_filename,
                mime_type=attachment.mime_type,
                size_bytes=attachment.size_bytes,
                message_id=attachment.message_id,
                created_at=attachment.created_at,
            )
            for attachment in attachments
        ],
    )


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository | None = None,
        resource_repository: ResourceRepository | None = None,
        attachment_repository: AttachmentRepository | None = None,
    ) -> None:
        self.repository = repository or ConversationRepository()
        self.resource_repository = resource_repository or ResourceRepository()
        self.attachment_repository = attachment_repository or AttachmentRepository()

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
        if not conversations:
            return []
        agents = self.resource_repository.list_visible(
            session,
            user_id=user_id,
            resource_type="agent",
            include_unavailable=True,
            resource_ids={
                item.conversation.agent_id for item in conversations
            },
        )
        agents_by_id = {agent.id: agent for agent in agents}
        return [
            self._history_item(
                item.conversation,
                agent=agents_by_id.get(item.conversation.agent_id),
                last_message=(
                    item.last_message
                    if item.last_message is not None
                    else item.conversation.title
                ),
            )
            for item in conversations
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
        attachments = self.attachment_repository.list_for_messages(
            session,
            user_id=user_id,
            message_ids=[message.id for message in messages],
        )
        attachments_by_message: dict[str, list[models.Attachment]] = {}
        for attachment in attachments:
            if attachment.message_id is not None:
                attachments_by_message.setdefault(attachment.message_id, []).append(
                    attachment
                )
        agent = self.resource_repository.get_visible(
            session,
            user_id=conversation.user_id,
            resource_id=conversation.agent_id,
            resource_type="agent",
            include_unavailable=True,
        )
        history_item = self._history_item(
            conversation,
            agent=agent,
            last_message=_last_message(conversation, messages),
        )
        return ConversationDetailResponse(
            **history_item.model_dump(),
            messages=[
                conversation_message_response(
                    message,
                    attachments_by_message.get(message.id, ()),
                )
                for message in messages
            ],
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
            title=title.strip(),
        )
        if conversation is None:
            return None
        messages = self.repository.list_messages(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
        )
        agent = self.resource_repository.get_visible(
            session,
            user_id=conversation.user_id,
            resource_id=conversation.agent_id,
            resource_type="agent",
            include_unavailable=True,
        )
        return self._history_item(
            conversation,
            agent=agent,
            last_message=_last_message(conversation, messages),
        )

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

    @staticmethod
    def _history_item(
        conversation: models.Conversation,
        *,
        agent: models.Resource | None,
        last_message: str,
    ) -> ConversationHistoryItemResponse:
        agent_response = ConversationAgentResponse(
            id=conversation.agent_id,
            name=agent.name if agent is not None else "Unavailable agent",
            is_available=(
                agent is not None
                and agent.is_active
                and agent.deleted_at is None
            ),
        )
        model_override = (
            ModelRef.model_validate(conversation.model_override_json)
            if conversation.model_override_json is not None
            else None
        )
        return ConversationHistoryItemResponse(
            id=conversation.id,
            title=conversation.title.strip() or "New conversation",
            href=f"/chat?session={conversation.id}",
            agent=agent_response,
            model_override=model_override,
            status=conversation.status,
            started_at=conversation.created_at,
            updated_at=conversation.updated_at,
            last_message=_excerpt(last_message),
        )


def _last_message(
    conversation: models.Conversation,
    messages: list[models.Message],
) -> str:
    for message in reversed(messages):
        if message.content.strip():
            return message.content
    return conversation.title


def _excerpt(value: str, max_length: int = 160) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1]}..."
