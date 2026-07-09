from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, get_user_scope
from app.core.errors import not_found
from app.core.user_scope import UserScope
from app.db import models
from app.schemas.reports import ReportDetailResponse, ReportListResponse, ReportResponse

router = APIRouter(prefix="/api/reports")


@router.get("", response_model=ReportListResponse)
def list_reports(
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ReportListResponse:
    reports = [
        report
        for conversation in _completed_interview_conversations(session, scope.user_id)
        if (report := _report_from_conversation(session, scope.user_id, conversation)) is not None
    ]
    return ReportListResponse(reports=reports)


@router.get("/{report_id}", response_model=ReportDetailResponse)
def get_report(
    report_id: str,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ReportDetailResponse:
    from app.repositories.artifact_repository import ArtifactRepository
    from app.services.artifact_service import InvalidFrontMatter
    from app.services.workspace_service import UnsafeWorkspacePath
    from app.services.artifact_service import ArtifactService
    from app.services.workspace_service import WorkspaceService

    artifact = ArtifactRepository().get(session, user_id=scope.user_id, artifact_id=report_id)
    if artifact is None or artifact.kind != "report":
        raise not_found("report_not_found", "Report not found.")
    report = _report_for_artifact(session, scope.user_id, artifact)
    if report is None:
        raise not_found("report_not_found", "Report not found.")
    workspace = WorkspaceService(
        scope.workspace_root,
        default_manifest_path=scope.default_manifest_path,
    )
    workspace.initialize()
    try:
        content = ArtifactService(workspace).read_markdown(artifact.relative_path).body
    except (FileNotFoundError, InvalidFrontMatter, UnsafeWorkspacePath) as exc:
        raise not_found("report_not_found", "Report artifact not found.") from exc
    return ReportDetailResponse(report=report, content=content)


def _completed_interview_conversations(session: Session, user_id: int) -> list[models.Conversation]:
    return list(
        session.scalars(
            select(models.Conversation)
            .where(
                models.Conversation.user_id == user_id,
                models.Conversation.kind == "interview",
                models.Conversation.deleted_at.is_(None),
                models.Conversation.status == "completed",
            )
            .order_by(models.Conversation.updated_at.desc())
        )
    )


def _report_from_conversation(
    session: Session,
    user_id: int,
    conversation: models.Conversation,
) -> ReportResponse | None:
    from app.repositories.artifact_repository import ArtifactRepository

    report_id = _optional_str((conversation.summary_json or {}).get("report_artifact_id"))
    if not report_id:
        return None
    artifact = ArtifactRepository().get(session, user_id=user_id, artifact_id=report_id)
    if artifact is None or artifact.kind != "report":
        return None
    return _report_response(conversation, artifact)


def _report_for_artifact(
    session: Session,
    user_id: int,
    artifact: models.Artifact,
) -> ReportResponse | None:
    conversations = _completed_interview_conversations(session, user_id)
    for conversation in conversations:
        if (conversation.summary_json or {}).get("report_artifact_id") == artifact.id:
            return _report_response(conversation, artifact)
    return None


def _report_response(
    conversation: models.Conversation,
    artifact: models.Artifact,
) -> ReportResponse:
    summary = conversation.summary_json or {}
    weaknesses = summary.get("weaknesses")
    return ReportResponse(
        id=artifact.id,
        session_id=conversation.id,
        report_path=artifact.relative_path,
        summary=_optional_str(summary.get("report_summary")) or _optional_str(summary.get("last_message")) or "",
        weaknesses=[item for item in weaknesses if isinstance(item, str)]
        if isinstance(weaknesses, list)
        else [],
        created_at=artifact.created_at,
    )


def _optional_str(value: object) -> str:
    return value if isinstance(value, str) else ""
