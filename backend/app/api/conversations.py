from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.session import session_scope
from app.schemas.conversations import ConversationDetailResponse, ConversationListResponse
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
