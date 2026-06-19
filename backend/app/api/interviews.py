from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.session import session_scope
from app.schemas.interviews import (
    AnswerFeedbackResponse,
    AnswerRequest,
    InterviewConfigIn,
    InterviewConfigResponse,
    InterviewSessionCreatedResponse,
    InterviewSessionDetailResponse,
    InterviewTurnResponse,
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


@router.post("/interview-sessions/{session_id}/answer", response_model=AnswerFeedbackResponse)
def submit_answer(
    session_id: str, answer: AnswerRequest, session: Session = Depends(get_session)
) -> AnswerFeedbackResponse:
    feedback = InterviewService().submit_answer(session, session_id, answer.answer)
    return AnswerFeedbackResponse.model_validate(feedback.model_dump())


@router.post(
    "/interview-sessions/{session_id}/follow-up-answer",
    response_model=InterviewTurnResponse,
)
def submit_follow_up_answer(
    session_id: str, answer: AnswerRequest, session: Session = Depends(get_session)
) -> InterviewTurnResponse:
    turn = InterviewService().submit_follow_up_answer(session, session_id, answer.answer)
    return InterviewTurnResponse.model_validate(turn)


@router.post("/interview-sessions/{session_id}/next-question", response_model=InterviewSessionCreatedResponse)
def next_question(
    session_id: str, session: Session = Depends(get_session)
) -> InterviewSessionCreatedResponse:
    interview_session, turn = InterviewService().next_question(session, session_id)
    return InterviewSessionCreatedResponse(session=interview_session, turn=turn)
