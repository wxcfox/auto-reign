from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, get_user_scope
from app.core.errors import not_found
from app.core.user_scope import UserScope
from app.schemas.conversations import (
    ConversationDeleteResponse,
    ConversationDetailResponse,
    ConversationHistoryItemResponse,
    ConversationListResponse,
    ConversationRenameRequest,
)


router = APIRouter(prefix="/api/conversations")


@router.get("", response_model=ConversationListResponse)
def list_conversations(
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ConversationListResponse:
    from app.services.conversation_service import ConversationService

    return ConversationListResponse(
        conversations=ConversationService().list_conversations(session)
    )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(
    conversation_id: str,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ConversationDetailResponse:
    from app.services.conversation_service import ConversationService

    detail = ConversationService().get_conversation(session, conversation_id)
    if detail is None:
        raise not_found("conversation_not_found", "Conversation not found.")
    return detail


@router.patch("/{conversation_id}", response_model=ConversationHistoryItemResponse)
def rename_conversation(
    conversation_id: str,
    payload: ConversationRenameRequest,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ConversationHistoryItemResponse:
    from app.services.conversation_service import ConversationService

    renamed = ConversationService().rename_conversation(session, conversation_id, payload.title)
    if renamed is None:
        raise not_found("conversation_not_found", "Conversation not found.")
    return renamed


@router.delete("/{conversation_id}", response_model=ConversationDeleteResponse)
def delete_conversation(
    conversation_id: str,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ConversationDeleteResponse:
    from app.services.conversation_service import ConversationService

    deleted = ConversationService().delete_conversation(session, conversation_id)
    if not deleted:
        raise not_found("conversation_not_found", "Conversation not found.")
    return ConversationDeleteResponse(id=conversation_id, status="deleted")
