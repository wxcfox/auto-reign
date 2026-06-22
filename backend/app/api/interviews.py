import json
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.session import session_scope
from app.repositories.artifact_repository import ArtifactRepository
from app.schemas.interviews import (
    AnswerFeedbackResponse,
    AnswerRequest,
    FollowUpFeedbackResponse,
    InterviewConfigIn,
    InterviewConfigResponse,
    InterviewSessionCreatedResponse,
    InterviewSessionDetailResponse,
)
from app.schemas.reports import ReportResponse
from app.services.index_service import IndexService
from app.services.interview_service import InterviewService

router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _http_error_payload(error: HTTPException) -> dict[str, Any]:
    if isinstance(error.detail, dict):
        return {
            "code": error.detail.get("code", "request_failed"),
            "message": error.detail.get("message", "Request failed."),
            "status_code": error.status_code,
        }
    return {
        "code": "request_failed",
        "message": str(error.detail),
        "status_code": error.status_code,
    }


def _streaming_response(
    events: Iterator,
    serialize_result: Callable[[dict[str, Any]], dict[str, Any]],
    session: Session,
) -> StreamingResponse:
    def body() -> Iterator[str]:
        try:
            for item in events:
                data = serialize_result(item.data) if item.event == "result" else item.data
                yield _sse_event(item.event, data)
        except HTTPException as error:
            session.rollback()
            yield _sse_event("error", _http_error_payload(error))
        except Exception:
            session.rollback()
            yield _sse_event(
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


def _session_created_payload(data: dict[str, Any]) -> dict[str, Any]:
    return InterviewSessionCreatedResponse(
        session=data["session"],
        turn=data["turn"],
    ).model_dump(mode="json")


def _answer_feedback_payload(data: dict[str, Any]) -> dict[str, Any]:
    return AnswerFeedbackResponse.model_validate(data).model_dump(mode="json")


def _follow_up_feedback_payload(data: dict[str, Any]) -> dict[str, Any]:
    return FollowUpFeedbackResponse.model_validate(data).model_dump(mode="json")


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
    config_in: InterviewConfigIn, request: Request, session: Session = Depends(get_session)
) -> InterviewSessionCreatedResponse:
    IndexService().ensure_current(
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    interview_session, turn = InterviewService().create_session(session, config_in)
    return InterviewSessionCreatedResponse(
        session=interview_session,
        turn=turn,
    )


@router.post("/interview-sessions/stream")
def create_session_stream(
    config_in: InterviewConfigIn, request: Request, session: Session = Depends(get_session)
) -> StreamingResponse:
    IndexService().ensure_current(
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    events = InterviewService().stream_create_session(session, config_in)
    return _streaming_response(events, _session_created_payload, session)


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


@router.post("/interview-sessions/{session_id}/answer/stream")
def submit_answer_stream(
    session_id: str, answer: AnswerRequest, session: Session = Depends(get_session)
) -> StreamingResponse:
    events = InterviewService().stream_submit_answer(session, session_id, answer.answer)
    return _streaming_response(events, _answer_feedback_payload, session)


@router.post(
    "/interview-sessions/{session_id}/follow-up-answer",
    response_model=FollowUpFeedbackResponse,
)
def submit_follow_up_answer(
    session_id: str, answer: AnswerRequest, session: Session = Depends(get_session)
) -> FollowUpFeedbackResponse:
    feedback = InterviewService().submit_follow_up_answer(session, session_id, answer.answer)
    return FollowUpFeedbackResponse.model_validate(feedback.model_dump())


@router.post("/interview-sessions/{session_id}/follow-up-answer/stream")
def submit_follow_up_answer_stream(
    session_id: str, answer: AnswerRequest, session: Session = Depends(get_session)
) -> StreamingResponse:
    events = InterviewService().stream_submit_follow_up_answer(session, session_id, answer.answer)
    return _streaming_response(events, _follow_up_feedback_payload, session)


@router.post("/interview-sessions/{session_id}/next-question", response_model=InterviewSessionCreatedResponse)
def next_question(
    session_id: str, request: Request, session: Session = Depends(get_session)
) -> InterviewSessionCreatedResponse:
    IndexService().ensure_current(
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    interview_session, turn = InterviewService().next_question(session, session_id)
    return InterviewSessionCreatedResponse(session=interview_session, turn=turn)


@router.post("/interview-sessions/{session_id}/next-question/stream")
def next_question_stream(
    session_id: str, request: Request, session: Session = Depends(get_session)
) -> StreamingResponse:
    IndexService().ensure_current(
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    events = InterviewService().stream_next_question(session, session_id)
    return _streaming_response(events, _session_created_payload, session)


@router.post("/interview-sessions/{session_id}/finish")
def finish_session(session_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    interview_session, report = InterviewService().finish_session(session, session_id)
    return {
        "session": InterviewSessionDetailResponse(
            session=interview_session,
            config=interview_session.config,
            turns=interview_session.turns,
        ).session,
        "report": ReportResponse.model_validate(report),
    }
