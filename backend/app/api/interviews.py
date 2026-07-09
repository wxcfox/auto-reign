import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, get_user_scope
from app.api.sse import http_error_payload, sse_event
from app.core.errors import not_found
from app.core.user_scope import UserScope
from app.schemas.interviews import (
    AnswerFeedbackResponse,
    AnswerRequest,
    FollowUpFeedbackResponse,
    InterviewConfigIn,
    InterviewConfigResponse,
    InterviewSessionCreatedResponse,
    InterviewSessionDetailResponse,
    NextQuestionRequest,
)
from app.schemas.reports import ReportResponse

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def _ensure_index_current_best_effort(
    request: Request,
    scope: UserScope,
    session: Session,
) -> None:
    from app.repositories.artifact_repository import ArtifactRepository
    from app.repositories.vector_store import VectorStoreError
    from app.services.index_service import IndexService

    workspace, _ = _workspace_services(scope)
    try:
        IndexService().ensure_current(
            request.app.state.session_factory,
            workspace,
            ArtifactRepository(),
            user_id=scope.user_id,
            qdrant_prefix=scope.qdrant_prefix,
        )
        session.expire_all()
    except VectorStoreError as exc:
        logger.info("Workspace index refresh unavailable for interview flow: %s", exc)


def _streaming_response(
    events: Iterator,
    serialize_result: Callable[[dict[str, Any]], dict[str, Any]],
    session: Session,
) -> StreamingResponse:
    def body() -> Iterator[str]:
        try:
            for item in events:
                data = serialize_result(item.data) if item.event == "result" else item.data
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


def _session_created_payload(data: dict[str, Any]) -> dict[str, Any]:
    return InterviewSessionCreatedResponse(
        session=data["session"],
        turn=data["turn"],
    ).model_dump(mode="json")


def _answer_feedback_payload(data: dict[str, Any]) -> dict[str, Any]:
    return AnswerFeedbackResponse.model_validate(data).model_dump(mode="json")


def _follow_up_feedback_payload(data: dict[str, Any]) -> dict[str, Any]:
    return FollowUpFeedbackResponse.model_validate(data).model_dump(mode="json")


def _finish_session_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "session": InterviewSessionDetailResponse(
            session=data["session"],
            config=data["session"].config,
            turns=data["session"].turns,
        ).session.model_dump(mode="json"),
        "report": ReportResponse.model_validate(data["report"]).model_dump(mode="json"),
    }


async def _next_question_intent(request: Request) -> str:
    raw_body = await request.body()
    if not raw_body or not raw_body.strip():
        return ""
    text = raw_body.decode("utf-8", errors="ignore").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:2000]
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload[:2000]
    if isinstance(payload, dict):
        return NextQuestionRequest.model_validate(payload).intent[:2000]
    raise HTTPException(status_code=422, detail="Invalid next question request body.")


def _workspace_services(scope: UserScope) -> tuple[Any, Any]:
    from app.services.artifact_service import ArtifactService
    from app.services.workspace_service import WorkspaceService

    workspace = WorkspaceService(
        scope.workspace_root,
        default_manifest_path=scope.default_manifest_path,
    )
    workspace.initialize()
    return workspace, ArtifactService(workspace)


def _interview_service(scope: UserScope) -> Any:
    from app.services.interview_service import InterviewService

    workspace, artifact_service = _workspace_services(scope)
    return InterviewService(
        user_id=scope.user_id,
        artifact_service=artifact_service,
        workspace_service=workspace,
    )


@router.get("/interview-configs/last", response_model=InterviewConfigResponse)
def get_last_config(
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> InterviewConfigResponse:
    config = _interview_service(scope).get_last_config(session)
    return InterviewConfigResponse.model_validate(config)


@router.put("/interview-configs/last", response_model=InterviewConfigResponse)
def save_last_config(
    config_in: InterviewConfigIn,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> InterviewConfigResponse:
    config = _interview_service(scope).save_last_config(session, config_in)
    return InterviewConfigResponse.model_validate(config)


@router.post("/interview-sessions", response_model=InterviewSessionCreatedResponse)
def create_session(
    config_in: InterviewConfigIn,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> InterviewSessionCreatedResponse:
    _ensure_index_current_best_effort(request, scope, session)
    interview_session, turn = _interview_service(scope).create_session(session, config_in)
    return InterviewSessionCreatedResponse(
        session=interview_session,
        turn=turn,
    )


@router.post("/interview-sessions/stream")
def create_session_stream(
    config_in: InterviewConfigIn,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> StreamingResponse:
    _ensure_index_current_best_effort(request, scope, session)
    events = _interview_service(scope).stream_create_session(session, config_in)
    return _streaming_response(events, _session_created_payload, session)


@router.get("/interview-sessions/{session_id}", response_model=InterviewSessionDetailResponse)
def get_session_detail(
    session_id: str,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> InterviewSessionDetailResponse:
    detail = _interview_service(scope).get_session_detail(session, session_id)
    if detail is None:
        raise not_found("session_not_found", "Interview session not found.")
    interview_session, config, turns = detail
    return InterviewSessionDetailResponse(session=interview_session, config=config, turns=turns)


@router.post("/interview-sessions/{session_id}/answer", response_model=AnswerFeedbackResponse)
def submit_answer(
    session_id: str,
    answer: AnswerRequest,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> AnswerFeedbackResponse:
    feedback = _interview_service(scope).submit_answer(session, session_id, answer.answer)
    return AnswerFeedbackResponse.model_validate(feedback.model_dump())


@router.post("/interview-sessions/{session_id}/answer/stream")
def submit_answer_stream(
    session_id: str,
    answer: AnswerRequest,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> StreamingResponse:
    events = _interview_service(scope).stream_submit_answer(session, session_id, answer.answer)
    return _streaming_response(events, _answer_feedback_payload, session)


@router.post(
    "/interview-sessions/{session_id}/follow-up-answer",
    response_model=FollowUpFeedbackResponse,
)
def submit_follow_up_answer(
    session_id: str,
    answer: AnswerRequest,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> FollowUpFeedbackResponse:
    feedback = _interview_service(scope).submit_follow_up_answer(
        session, session_id, answer.answer
    )
    return FollowUpFeedbackResponse.model_validate(feedback.model_dump())


@router.post("/interview-sessions/{session_id}/follow-up-answer/stream")
def submit_follow_up_answer_stream(
    session_id: str,
    answer: AnswerRequest,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> StreamingResponse:
    events = _interview_service(scope).stream_submit_follow_up_answer(
        session, session_id, answer.answer
    )
    return _streaming_response(events, _follow_up_feedback_payload, session)


@router.post("/interview-sessions/{session_id}/next-question", response_model=InterviewSessionCreatedResponse)
async def next_question(
    session_id: str,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> InterviewSessionCreatedResponse:
    _ensure_index_current_best_effort(request, scope, session)
    interview_session, turn = _interview_service(scope).next_question(
        session,
        session_id,
        intent=await _next_question_intent(request),
    )
    return InterviewSessionCreatedResponse(session=interview_session, turn=turn)


@router.post("/interview-sessions/{session_id}/next-question/stream")
async def next_question_stream(
    session_id: str,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> StreamingResponse:
    _ensure_index_current_best_effort(request, scope, session)
    events = _interview_service(scope).stream_next_question(
        session,
        session_id,
        intent=await _next_question_intent(request),
    )
    return _streaming_response(events, _session_created_payload, session)


@router.post("/interview-sessions/{session_id}/finish")
def finish_session(
    session_id: str,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> dict[str, object]:
    interview_session, report = _interview_service(scope).finish_session(session, session_id)
    return {
        "session": InterviewSessionDetailResponse(
            session=interview_session,
            config=interview_session.config,
            turns=interview_session.turns,
        ).session,
        "report": ReportResponse.model_validate(report),
    }


@router.post("/interview-sessions/{session_id}/finish/stream")
def finish_session_stream(
    session_id: str,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> StreamingResponse:
    events = _interview_service(scope).stream_finish_session(session, session_id)
    return _streaming_response(events, _finish_session_payload, session)
