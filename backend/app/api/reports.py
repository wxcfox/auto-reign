from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.db.session import session_scope
from app.repositories.database import ReportRepository
from app.schemas.reports import ReportDetailResponse, ReportListResponse, ReportResponse

router = APIRouter(prefix="/api/reports")


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


@router.get("", response_model=ReportListResponse)
def list_reports(session: Session = Depends(get_session)) -> ReportListResponse:
    reports = ReportRepository().list(session)
    return ReportListResponse(reports=[ReportResponse.model_validate(report) for report in reports])


@router.get("/{report_id}", response_model=ReportDetailResponse)
def get_report(
    report_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> ReportDetailResponse:
    report = ReportRepository().get(session, report_id)
    if report is None:
        raise not_found("report_not_found", "Report not found.")
    try:
        content = request.app.state.artifact_service.read_markdown(report.report_path).body
    except FileNotFoundError as exc:
        raise not_found("report_not_found", "Report artifact not found.") from exc
    return ReportDetailResponse(report=ReportResponse.model_validate(report), content=content)
