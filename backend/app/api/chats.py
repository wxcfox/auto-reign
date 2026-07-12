from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, get_user_scope
from app.api.sse import http_error_payload, sse_event
from app.core.user_scope import UserScope
from app.schemas.chats import ChatMessageRequest, ChatMessageResult
from app.schemas.conversations import ConversationMessageResponse
from app.services.chat_service import ChatService


router = APIRouter(prefix="/api/chats")


@router.post("/stream")
def stream_chat_message(
    payload: ChatMessageRequest,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> StreamingResponse:
    events = ChatService().stream_message(session, user_id=scope.user_id, request=payload)

    def body() -> Iterator[str]:
        try:
            for item in events:
                data = item.data
                if item.event == "result":
                    message = item.data["message"]
                    data = ChatMessageResult(
                        conversation_id=str(item.data["conversation_id"]),
                        message=ConversationMessageResponse(
                            id=message.id,
                            role=message.role,
                            message_type=message.message_type,
                            content=message.content,
                            created_at=message.created_at,
                            metadata=message.metadata_json,
                        ),
                    ).model_dump(mode="json")
                yield sse_event(item.event, data)
        except HTTPException as error:
            session.rollback()
            yield sse_event("error", http_error_payload(error))
        except Exception:
            session.rollback()
            yield sse_event(
                "error",
                {
                    "code": "stream_failed",
                    "message": "The streaming response failed.",
                    "status_code": 502,
                },
            )

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
