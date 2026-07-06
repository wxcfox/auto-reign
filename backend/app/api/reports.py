from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, get_user_scope
from app.core.errors import not_found
from app.core.user_scope import UserScope
from app.schemas.reports import ReportDetailResponse, ReportListResponse, ReportResponse

router = APIRouter(prefix="/api/reports")


@router.get("", response_model=ReportListResponse)
def list_reports(
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ReportListResponse:
    from app.repositories.database import ReportRepository

    reports = ReportRepository().list(session)
    return ReportListResponse(reports=[ReportResponse.model_validate(report) for report in reports])


@router.get("/{report_id}", response_model=ReportDetailResponse)
def get_report(
    report_id: str,
    request: Request,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ReportDetailResponse:
    from app.repositories.database import ReportRepository
    from app.services.artifact_service import InvalidFrontMatter
    from app.services.workspace_service import UnsafeWorkspacePath

    report = ReportRepository().get(session, report_id)
    if report is None:
        raise not_found("report_not_found", "Report not found.")
    try:
        content = request.app.state.artifact_service.read_markdown(report.report_path).body
    except (FileNotFoundError, InvalidFrontMatter, UnsafeWorkspacePath) as exc:
        raise not_found("report_not_found", "Report artifact not found.") from exc
    return ReportDetailResponse(report=ReportResponse.model_validate(report), content=content)
