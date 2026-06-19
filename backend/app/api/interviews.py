from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.session import session_scope
from app.schemas.interviews import (
    InterviewConfigIn,
    InterviewConfigResponse,
    InterviewSessionCreatedResponse,
    InterviewSessionDetailResponse,
)
from app.services.interview_service import InterviewService

router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


@router.get("/interview-configs/last", response_model=InterviewConfigResponse)
def get_last_config(session: Session = Depends(get_session)) -> InterviewConfigResponse:
    config = InterviewService().get_last_config(session)
    return InterviewConfigResponse.model_validate(config)


@router.put("/interview-configs/last", response_model=InterviewConfigResponse)
def save_last_config(
    config_in: InterviewConfigIn, session: Session = Depends(get_session)
) -> InterviewConfigResponse:
    config = InterviewService().save_last_config(session, config_in)
    return InterviewConfigResponse.model_validate(config)


@router.post("/interview-sessions", response_model=InterviewSessionCreatedResponse)
def create_session(
    config_in: InterviewConfigIn, session: Session = Depends(get_session)
) -> InterviewSessionCreatedResponse:
    interview_session, turn = InterviewService().create_session(session, config_in)
    return InterviewSessionCreatedResponse(
        session=interview_session,
        turn=turn,
    )


@router.get("/interview-sessions/{session_id}", response_model=InterviewSessionDetailResponse)
def get_session_detail(
    session_id: str, session: Session = Depends(get_session)
) -> InterviewSessionDetailResponse:
    detail = InterviewService().get_session_detail(session, session_id)
    if detail is None:
        raise not_found("session_not_found", "Interview session not found.")
    interview_session, config, turns = detail
    return InterviewSessionDetailResponse(session=interview_session, config=config, turns=turns)
