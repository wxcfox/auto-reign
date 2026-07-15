from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import SessionDep, get_current_user
from app.api.sse import GenerationStreamingResponse
from app.core.errors import conflict, not_found
from app.db import models
from app.db.session import session_scope
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.conversations import (
    ConversationDeleteResponse,
    ConversationDetailResponse,
    ConversationHistoryItemResponse,
    ConversationListResponse,
    ConversationModelPutRequest,
    ConversationRenameRequest,
    ConversationSendRequest,
)
from app.services.agent_service import AgentService
from app.services.conversation_service import ConversationService
from app.services.generation_service import GenerationEvent


router = APIRouter(prefix="/api/conversations")


def _generation_response(
    events: Iterator[GenerationEvent],
) -> StreamingResponse:
    return GenerationStreamingResponse(events)


@router.post("/stream")
def stream_conversation(
    payload: ConversationSendRequest,
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> StreamingResponse:
    events = request.app.state.generation_service.stream_turn_for_api(
        user_id=current_user.id,
        request=payload,
    )
    return _generation_response(events)


@router.get("", response_model=ConversationListResponse)
def list_conversations(
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
) -> ConversationListResponse:
    return ConversationListResponse(
        conversations=ConversationService().list_conversations(
            session,
            user_id=current_user.id,
        )
    )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(
    conversation_id: str,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
) -> ConversationDetailResponse:
    detail = ConversationService().get_conversation(
        session,
        conversation_id,
        user_id=current_user.id,
    )
    if detail is None:
        raise not_found("conversation_not_found", "Conversation not found.")
    return detail


@router.patch("/{conversation_id}", response_model=ConversationHistoryItemResponse)
def rename_conversation(
    conversation_id: str,
    payload: ConversationRenameRequest,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
) -> ConversationHistoryItemResponse:
    renamed = ConversationService().rename_conversation(
        session,
        conversation_id,
        payload.title,
        user_id=current_user.id,
    )
    if renamed is None:
        raise not_found("conversation_not_found", "Conversation not found.")
    return renamed


@router.delete("/{conversation_id}", response_model=ConversationDeleteResponse)
def delete_conversation(
    conversation_id: str,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
) -> ConversationDeleteResponse:
    deleted = ConversationService().delete_conversation(
        session,
        conversation_id,
        user_id=current_user.id,
    )
    if not deleted:
        raise not_found("conversation_not_found", "Conversation not found.")
    return ConversationDeleteResponse(id=conversation_id, status="deleted")


@router.put(
    "/{conversation_id}/model",
    response_model=ConversationDetailResponse,
)
def put_conversation_model(
    conversation_id: str,
    payload: ConversationModelPutRequest,
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> ConversationDetailResponse:
    repository = ConversationRepository()
    conversation_service = ConversationService(repository=repository)
    agent_service = AgentService(settings=request.app.state.settings)
    with session_scope(request.app.state.session_factory) as session:
        conversation = repository.get_for_update(
            session,
            user_id=current_user.id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise not_found(
                "conversation_not_found",
                "Conversation not found.",
            )
        if conversation.status != "idle":
            raise conflict(
                "generation_in_progress",
                "This conversation already has an active generation.",
            )
        agent = agent_service.resolve_for_turn(
            session,
            user_id=current_user.id,
            agent_id=conversation.agent_id,
        )
        agent_service.resolve_model(
            agent=agent,
            conversation_override=payload.model_override,
        )
        repository.set_model_override(
            session,
            conversation=conversation,
            model_override=payload.model_override,
        )
    with session_scope(request.app.state.session_factory) as session:
        projected = conversation_service.get_conversation(
            session,
            conversation_id,
            user_id=current_user.id,
        )
        if projected is None:
            raise not_found(
                "conversation_not_found",
                "Conversation not found.",
            )
        return projected


@router.post(
    "/{conversation_id}/messages/{message_id}/retry/stream",
)
def retry_conversation_message(
    conversation_id: str,
    message_id: str,
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> StreamingResponse:
    events = request.app.state.generation_service.retry_turn_for_api(
        user_id=current_user.id,
        conversation_id=conversation_id,
        failed_message_id=message_id,
    )
    return _generation_response(events)
