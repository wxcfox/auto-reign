from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    report_path: str
    summary: str
    weaknesses: list[str] = Field(default_factory=list)
    created_at: datetime


class ReportDetailResponse(BaseModel):
    report: ReportResponse
    content: str


class ReportListResponse(BaseModel):
    reports: list[ReportResponse]
