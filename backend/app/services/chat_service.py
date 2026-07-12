from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.db import models
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.chats import ChatMessageRequest
from app.services.model_service import ModelService


CHAT_HISTORY_MAX_MESSAGES = 40
CHAT_HISTORY_MAX_CHARS = 60000


@dataclass(frozen=True)
class ChatStreamEvent:
    event: str
    data: dict[str, object]


class ChatService:
    def __init__(
        self,
        *,
        repository: ConversationRepository | None = None,
        model_service: ModelService | None = None,
    ) -> None:
        self.repository = repository or ConversationRepository()
        self.model_service = model_service or ModelService()

    def stream_message(
        self,
        session: Session,
        *,
        user_id: int,
        request: ChatMessageRequest,
    ) -> Iterator[ChatStreamEvent]:
        text = request.text.strip()
        if not text:
            raise bad_request("chat_message_empty", "Chat message text is required.")
        conversation = self._conversation(session, user_id=user_id, request=request)
        history = self._model_history(
            session,
            user_id=user_id,
            conversation_id=conversation.id if conversation is not None else None,
            current_message=text,
        )
        config = conversation.config_json if conversation is not None else {}
        provider = request.provider or _config_string(config, "provider")
        model = request.model or _config_string(config, "model")

        def events() -> Iterator[ChatStreamEvent]:
            response = ""
            for chunk in self.model_service.stream_messages(
                history,
                provider=provider or None,
                model=model or None,
            ):
                response += chunk
                yield ChatStreamEvent(event="delta", data={"text": chunk})
            response = response.strip()
            persisted_conversation = conversation or self.repository.create(
                session,
                user_id=user_id,
                kind="chat",
                title=_conversation_title(text),
            )
            self.repository.add_message(
                session,
                user_id=user_id,
                conversation_id=persisted_conversation.id,
                role="user",
                message_type="chat_message",
                content=text,
            )
            assistant_message = self.repository.add_message(
                session,
                user_id=user_id,
                conversation_id=persisted_conversation.id,
                role="assistant",
                message_type="chat_message",
                content=response,
            )
            persisted_conversation.config_json = {
                **(persisted_conversation.config_json or {}),
                "language": request.language,
                "provider": provider,
                "model": model,
            }
            persisted_conversation.summary_json = {
                **(persisted_conversation.summary_json or {}),
                "last_message": response,
            }
            session.flush()
            yield ChatStreamEvent(
                event="result",
                data={
                    "conversation_id": persisted_conversation.id,
                    "message": assistant_message,
                },
            )

        return events()

    def _conversation(
        self,
        session: Session,
        *,
        user_id: int,
        request: ChatMessageRequest,
    ) -> models.Conversation | None:
        if request.conversation_id:
            conversation = self.repository.get(
                session,
                user_id=user_id,
                conversation_id=request.conversation_id,
                kind="chat",
            )
            if conversation is None:
                raise not_found("chat_not_found", "Chat conversation not found.")
            return conversation
        return None

    def _model_history(
        self,
        session: Session,
        *,
        user_id: int,
        conversation_id: str | None,
        current_message: str,
    ) -> list[dict[str, str]]:
        messages = (
            self.repository.list_messages(
                session,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if conversation_id is not None
            else []
        )
        model_messages = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"user", "assistant"}
        ]
        model_messages.append({"role": "user", "content": current_message})
        selected: list[dict[str, str]] = []
        total_chars = 0
        for message in reversed(model_messages):
            if len(selected) >= CHAT_HISTORY_MAX_MESSAGES:
                break
            if selected and total_chars + len(message["content"]) > CHAT_HISTORY_MAX_CHARS:
                break
            selected.append(message)
            total_chars += len(message["content"])
        return list(reversed(selected))


def _conversation_title(text: str) -> str:
    title = " ".join(text.split())
    return title[:80]


def _config_string(config: dict[str, object], key: str) -> str:
    value = config.get(key)
    return value if isinstance(value, str) else ""
