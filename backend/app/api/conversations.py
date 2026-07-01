from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.session import session_scope
from app.schemas.conversations import (
    ConversationDeleteResponse,
    ConversationDetailResponse,
    ConversationHistoryItemResponse,
    ConversationListResponse,
    ConversationRenameRequest,
)
from app.services.conversation_service import ConversationService


router = APIRouter(prefix="/api/conversations")


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


@router.get("", response_model=ConversationListResponse)
def list_conversations(session: Session = Depends(get_session)) -> ConversationListResponse:
    return ConversationListResponse(
        conversations=ConversationService().list_conversations(session)
    )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(
    conversation_id: str,
    session: Session = Depends(get_session),
) -> ConversationDetailResponse:
    detail = ConversationService().get_conversation(session, conversation_id)
    if detail is None:
        raise not_found("conversation_not_found", "Conversation not found.")
    return detail


@router.patch("/{conversation_id}", response_model=ConversationHistoryItemResponse)
def rename_conversation(
    conversation_id: str,
    payload: ConversationRenameRequest,
    session: Session = Depends(get_session),
) -> ConversationHistoryItemResponse:
    renamed = ConversationService().rename_conversation(session, conversation_id, payload.title)
    if renamed is None:
        raise not_found("conversation_not_found", "Conversation not found.")
    return renamed


@router.delete("/{conversation_id}", response_model=ConversationDeleteResponse)
def delete_conversation(
    conversation_id: str,
    session: Session = Depends(get_session),
) -> ConversationDeleteResponse:
    deleted = ConversationService().delete_conversation(session, conversation_id)
    if not deleted:
        raise not_found("conversation_not_found", "Conversation not found.")
    return ConversationDeleteResponse(id=conversation_id, status="deleted")
