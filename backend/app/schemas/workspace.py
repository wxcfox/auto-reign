from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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
