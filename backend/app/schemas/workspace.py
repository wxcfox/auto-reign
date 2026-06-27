from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.modeling import LearningNoteSummaryResult


ArtifactKind = Literal[
    "source",
    "extracted",
    "candidate_profile",
    "target_profile",
    "knowledge",
    "question_bank",
    "project",
    "interview_record",
    "high_frequency",
    "review_status",
    "practice",
    "mastery",
    "plan",
    "report",
]
ProcessingStatus = Literal[
    "pending",
    "processing",
    "completed",
    "failed",
    "needs_recovery",
]
IndexStatus = Literal["pending", "stale", "completed", "failed"]
Origin = Literal["human", "llm", "observed"]
EditedBy = Literal["system", "user"]
JobOperation = Literal[
    "ingest",
    "extract",
    "reindex",
    "update_profile",
    "update_knowledge",
    "archive_practice",
    "update_mastery",
    "update_plan",
    "generate_report",
]
JobStatus = Literal["pending", "running", "completed", "failed"]
MaterialType = Literal[
    "resume",
    "job_description",
    "project",
    "study_note",
    "interview_record",
    "mixed",
]
MasteryLevel = Literal["unrated", "weak", "basic", "fluent"]


class ArtifactFrontMatter(BaseModel):
    id: str
    kind: ArtifactKind
    language: str = "zh-CN"
    revision: int = 1
    created_at: datetime
    updated_at: datetime
    source_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    origin: Origin = "llm"
    edited_by: EditedBy = "system"
    recovery_required: bool = False
    recovery_reason: str | None = None


class SourceMeta(BaseModel):
    artifact_id: str
    source_filename: str
    media_type: str
    size_bytes: int
    content_hash: str
    uploaded_at: datetime
    relative_path: str
    language: str = "zh-CN"


class WorkspaceStatusResponse(BaseModel):
    schema_version: int
    language: str
    artifact_count: int
    initialized: bool = True


class UploadedSourceResponse(BaseModel):
    artifact_id: str
    relative_path: str
    duplicate: bool


class UploadMaterialsResponse(BaseModel):
    sources: list[UploadedSourceResponse]


class LearningNoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    language: str = "zh-CN"
    provider: str | None = None
    model: str | None = None


class RealInterviewRecordRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    language: str = "zh-CN"


class ArtifactSummaryResponse(BaseModel):
    id: str
    kind: str
    owner: str
    relative_path: str
    display_name: str
    revision: int
    processing_status: str
    index_status: str
    recovery_required: bool
    allowed_operations: list[str]
    created_at: datetime
    updated_at: datetime


class LearningNoteResponse(BaseModel):
    source: UploadedSourceResponse
    artifact: ArtifactSummaryResponse
    summary: LearningNoteSummaryResult
    card_markdown: str


class RealInterviewRecordResponse(BaseModel):
    raw_artifact: ArtifactSummaryResponse
    high_frequency_artifact: ArtifactSummaryResponse
    status_artifact: ArtifactSummaryResponse
    questions: list[str]
    weak_points: list[str]


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactSummaryResponse]


class PreparationTaskResponse(BaseModel):
    title: str
    reason: str
    source_artifact_id: str | None = None
    source_relative_path: str | None = None


class PreparationTasksResponse(BaseModel):
    tasks: list[PreparationTaskResponse]


class ArtifactDetailResponse(ArtifactSummaryResponse):
    body: str | None = None


class ArtifactDeleteResponse(BaseModel):
    id: str
    status: str


class ReplaceBodyRequest(BaseModel):
    expected_revision: int
    body: str
