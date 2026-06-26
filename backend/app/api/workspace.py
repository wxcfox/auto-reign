from __future__ import annotations

from collections.abc import Iterator

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.artifact_permissions import (
    ALLOWED_OPERATIONS,
    ArtifactPermissionError,
    assert_operation_allowed,
)
from app.core.errors import bad_request
from app.core.errors import conflict as conflict_error
from app.core.errors import not_found
from app.core.errors import service_unavailable
from app.db.session import session_scope
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.vector_store import VectorStoreError
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.artifact_service import ArtifactConflict
from app.services.ingestion_service import IngestionService, UploadItem
from app.services.index_service import IndexService
from app.services.model_service import LearningNoteSummaryResult, ModelService


router = APIRouter(prefix="/api/workspace")


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


class LearningNoteResponse(BaseModel):
    source: UploadedSourceResponse
    artifact: "ArtifactSummaryResponse"
    summary: LearningNoteSummaryResult
    card_markdown: str


class RealInterviewRecordRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    language: str = "zh-CN"


class RealInterviewRecordResponse(BaseModel):
    raw_artifact: "ArtifactSummaryResponse"
    high_frequency_artifact: "ArtifactSummaryResponse"
    status_artifact: "ArtifactSummaryResponse"
    questions: list[str]
    weak_points: list[str]


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


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


@router.get("", response_model=WorkspaceStatusResponse)
def workspace_status(session: Session = Depends(get_session)) -> WorkspaceStatusResponse:
    settings = WorkspaceSettingsRepository().get_or_create(session)
    artifacts = ArtifactRepository().list(session)
    return WorkspaceStatusResponse(
        schema_version=settings.schema_version,
        language=settings.language,
        artifact_count=len(artifacts),
    )


@router.get("/artifacts", response_model=ArtifactListResponse)
def list_artifacts(session: Session = Depends(get_session)) -> ArtifactListResponse:
    artifacts = ArtifactRepository().list(session)
    return ArtifactListResponse(artifacts=[_summary(artifact) for artifact in artifacts])


@router.get("/preparation-tasks", response_model=PreparationTasksResponse)
def preparation_tasks(
    request: Request,
    session: Session = Depends(get_session),
) -> PreparationTasksResponse:
    repository = ArtifactRepository()
    status = repository.get_by_relative_path(session, "review/status.md")
    if status is None:
        return PreparationTasksResponse(tasks=[])
    try:
        body = request.app.state.artifact_service.read_markdown(status.relative_path).body
    except Exception:
        return PreparationTasksResponse(tasks=[])
    sections = _markdown_sections(body)
    task_items = (
        _markdown_list_items(sections.get("当前重点") or "")
        or _markdown_list_items(sections.get("最近练习") or "")
        or _markdown_list_items(sections.get("最近整理") or "")
    )
    return PreparationTasksResponse(
        tasks=[
            PreparationTaskResponse(
                title=task,
                reason="来自复习状态",
                source_artifact_id=status.id,
                source_relative_path=status.relative_path,
            )
            for task in task_items[:3]
        ]
    )


@router.get("/artifacts/{artifact_id}", response_model=ArtifactDetailResponse)
def get_artifact(
    artifact_id: str, request: Request, session: Session = Depends(get_session)
) -> ArtifactDetailResponse:
    artifact = ArtifactRepository().get(session, artifact_id)
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found.")
    body: str | None = None
    if artifact.kind not in {"source"}:
        try:
            body = request.app.state.artifact_service.read_markdown(artifact.relative_path).body
        except Exception:
            body = None
    return ArtifactDetailResponse(**_summary(artifact).model_dump(), body=body)


@router.put("/artifacts/{artifact_id}/body", response_model=ArtifactSummaryResponse)
def replace_artifact_body(
    artifact_id: str,
    payload: ReplaceBodyRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> ArtifactSummaryResponse:
    repository = ArtifactRepository()
    artifact = repository.get(session, artifact_id)
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found.")
    try:
        assert_operation_allowed(artifact.kind, "replace_body")
    except ArtifactPermissionError as exc:
        if artifact.kind in {"source", "extracted", "practice", "mastery"}:
            raise HTTPException(status_code=403, detail={"code": "artifact_read_only", "message": str(exc)}) from exc
        raise bad_request("artifact_edit_invalid", str(exc)) from exc
    try:
        request.app.state.artifact_service.replace_body(
            artifact.relative_path,
            expected_revision=payload.expected_revision,
            body=payload.body,
            edited_by="user",
        )
    except ArtifactConflict as exc:
        raise conflict_error("artifact_revision_conflict", str(exc)) from exc
    request.app.state.workspace_service.rebuild_projection(
        session,
        repository,
        request.app.state.artifact_service,
    )
    updated = repository.get(session, artifact_id)
    return _summary(updated)


@router.delete("/artifacts/{artifact_id}", response_model=ArtifactDeleteResponse)
def delete_artifact(
    artifact_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> ArtifactDeleteResponse:
    repository = ArtifactRepository()
    artifact = repository.get(session, artifact_id)
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found.")
    workspace_settings = WorkspaceSettingsRepository().get_or_create(session)
    index_service = IndexService()
    target_collection = workspace_settings.active_collection or index_service.settings.qdrant_collection
    try:
        index_service.vector_store.delete_artifact_chunks(target_collection, artifact_id)
    except VectorStoreError as exc:
        raise service_unavailable(
            "vector_delete_failed",
            "Artifact chunks could not be removed from the vector index.",
        ) from exc
    try:
        request.app.state.artifact_service.delete_artifact_files(
            artifact.relative_path,
            artifact_id=artifact.id,
            remove_source_sidecar=artifact.kind == "source",
        )
    except OSError as exc:
        raise bad_request("artifact_delete_failed", "Artifact could not be deleted.") from exc
    request.app.state.workspace_service.rebuild_projection(
        session,
        repository,
        request.app.state.artifact_service,
    )
    return ArtifactDeleteResponse(id=artifact_id, status="deleted")


@router.post("/rebuild-projection", response_model=WorkspaceStatusResponse)
def rebuild_projection(request: Request, session: Session = Depends(get_session)) -> WorkspaceStatusResponse:
    request.app.state.workspace_service.rebuild_projection(
        session,
        ArtifactRepository(),
        request.app.state.artifact_service,
    )
    settings = WorkspaceSettingsRepository().get_or_create(session)
    artifacts = ArtifactRepository().list(session)
    return WorkspaceStatusResponse(
        schema_version=settings.schema_version,
        language=settings.language,
        artifact_count=len(artifacts),
    )


@router.post("/materials/upload", response_model=UploadMaterialsResponse)
async def upload_materials(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> UploadMaterialsResponse:
    uploads = [
        UploadItem(
            filename=file.filename or "material.txt",
            media_type=file.content_type or "application/octet-stream",
            content=await file.read(),
        )
        for file in files
    ]
    try:
        with session_scope(request.app.state.session_factory) as session:
            result = IngestionService().ingest_uploads(
                session,
                request.app.state.workspace_service,
                request.app.state.artifact_service,
                ArtifactRepository(),
                uploads,
            )
    except ValueError as exc:
        raise bad_request("material_upload_invalid", str(exc)) from exc
    background_tasks.add_task(
        IndexService().rebuild_index,
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    return UploadMaterialsResponse(
        sources=[UploadedSourceResponse(**source.__dict__) for source in result.sources]
    )


@router.post("/learning-notes/stream")
def record_learning_note_stream(
    payload: LearningNoteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> StreamingResponse:
    note = payload.text.strip()
    if not note:
        raise bad_request("learning_note_empty", "Learning note text is required.")

    def body() -> Iterator[str]:
        chunks: list[str] = []
        try:
            for chunk in ModelService().stream_learning_note_summary(
                note,
                language=payload.language,
                provider=payload.provider,
                model=payload.model,
            ):
                chunks.append(chunk)
                yield _sse_event("delta", {"text": chunk})
            assistant_message = "".join(chunks).strip()
            summary = _parse_learning_note_summary(assistant_message, note, payload.language)
            response = _persist_learning_note(
                note,
                payload.language,
                summary,
                request,
                background_tasks,
            )
            yield _sse_event("result", response.model_dump(mode="json"))
        except HTTPException as error:
            yield _sse_event("error", _http_error_payload(error))
        except Exception:
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


@router.post("/real-interview-records", response_model=RealInterviewRecordResponse)
def record_real_interview(
    payload: RealInterviewRecordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> RealInterviewRecordResponse:
    record = payload.text.strip()
    if not record:
        raise bad_request("real_interview_record_empty", "Real interview record text is required.")
    return _persist_real_interview_record(
        record,
        payload.language,
        request,
        background_tasks,
    )


def _persist_real_interview_record(
    record: str,
    language: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> RealInterviewRecordResponse:
    timestamp = datetime.now(UTC)
    questions = _extract_real_interview_questions(record)
    weak_points = _extract_real_interview_weak_points(record)
    raw_path = f"raw/{timestamp.strftime('%Y%m%d-%H%M%S-%f')}.md"
    raw_document = request.app.state.artifact_service.create_markdown(
        raw_path,
        kind="interview_record",
        language=language,
        body=_real_interview_record_body(record, questions, weak_points),
        origin="human",
        edited_by="user",
        now=timestamp,
    )
    raw_ref = f"artifact:{raw_document.front_matter.id}"
    high_frequency_path = "review/high-frequency.md"
    status_path = "review/status.md"
    _create_or_merge_high_frequency_card(
        request.app.state.artifact_service,
        high_frequency_path,
        questions=questions,
        weak_points=weak_points,
        language=language,
        source_ref=raw_ref,
        timestamp=timestamp,
    )
    _create_or_merge_review_status_from_real_interview(
        request.app.state.artifact_service,
        status_path,
        questions=questions,
        weak_points=weak_points,
        language=language,
        evidence_ref=raw_ref,
        timestamp=timestamp,
    )

    with session_scope(request.app.state.session_factory) as session:
        repository = ArtifactRepository()
        request.app.state.workspace_service.rebuild_projection(
            session,
            repository,
            request.app.state.artifact_service,
        )
        raw_artifact = repository.get(session, raw_document.front_matter.id)
        high_frequency_artifact = repository.get_by_relative_path(session, high_frequency_path)
        status_artifact = repository.get_by_relative_path(session, status_path)
        if raw_artifact is None or high_frequency_artifact is None or status_artifact is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "real_interview_projection_failed",
                    "message": "Real interview record was saved but projection failed.",
                },
            )
        response = RealInterviewRecordResponse(
            raw_artifact=_summary(raw_artifact),
            high_frequency_artifact=_summary(high_frequency_artifact),
            status_artifact=_summary(status_artifact),
            questions=questions,
            weak_points=weak_points,
        )

    background_tasks.add_task(
        IndexService().rebuild_index,
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    return response


def _persist_learning_note(
    note: str,
    language: str,
    summary: LearningNoteSummaryResult,
    request: Request,
    background_tasks: BackgroundTasks,
) -> LearningNoteResponse:
    timestamp = datetime.now(UTC)
    source = request.app.state.artifact_service.append_source(
        f"inbox/{timestamp.strftime('%Y-%m-%d')}.md",
        source_filename=f"{timestamp.strftime('%Y-%m-%d')}.md",
        media_type="text/markdown",
        content=_learning_note_inbox_entry(note, timestamp).encode("utf-8"),
        language=language,
        uploaded_at=timestamp,
    )
    source_ref = f"source:{source.artifact_id}"
    knowledge_path = f"knowledge/{_slug(summary.title)}.md"
    card_markdown = _learning_note_card(note, summary)
    _create_or_merge_learning_card(
        request.app.state.artifact_service,
        knowledge_path,
        card_markdown=card_markdown,
        summary=summary,
        language=language,
        source_ref=source_ref,
        timestamp=timestamp,
    )
    _create_or_merge_review_status_from_learning(
        request.app.state.artifact_service,
        "review/status.md",
        title=summary.title,
        source_ref=source_ref,
        language=language,
        timestamp=timestamp,
    )

    with session_scope(request.app.state.session_factory) as session:
        repository = ArtifactRepository()
        request.app.state.workspace_service.rebuild_projection(
            session,
            repository,
            request.app.state.artifact_service,
        )
        source_artifact = repository.get(session, source.artifact_id)
        knowledge_artifact = repository.get_by_relative_path(session, knowledge_path)
        if source_artifact is None or knowledge_artifact is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "learning_note_projection_failed",
                    "message": "Learning note was saved but projection failed.",
                },
            )
        response = LearningNoteResponse(
            source=UploadedSourceResponse(
                artifact_id=source_artifact.id,
                relative_path=source_artifact.relative_path,
                duplicate=False,
            ),
            artifact=_summary(knowledge_artifact),
            summary=summary,
            card_markdown=card_markdown,
        )

    background_tasks.add_task(
        IndexService().rebuild_index,
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    return response


@router.post("/rebuild-index")
def rebuild_index(request: Request) -> dict[str, str]:
    collection = IndexService().rebuild_index(
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    return {"status": "ok", "collection": collection}


def _summary(artifact) -> ArtifactSummaryResponse:
    return ArtifactSummaryResponse(
        id=artifact.id,
        kind=artifact.kind,
        owner=_owner(artifact),
        relative_path=artifact.relative_path,
        display_name=_display_name(artifact),
        revision=artifact.revision,
        processing_status=artifact.processing_status,
        index_status=artifact.index_status,
        recovery_required=artifact.recovery_required,
        allowed_operations=sorted(ALLOWED_OPERATIONS.get(artifact.kind, set())),
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
    )


def _display_name(artifact) -> str:
    if artifact.kind == "source" and artifact.source_filename:
        return artifact.source_filename
    return Path(artifact.relative_path).name


def _owner(artifact) -> str:
    if artifact.kind in {"candidate_profile", "target_profile"}:
        return "profile"
    if artifact.kind in {"mastery", "plan"}:
        return "state"
    parts = Path(artifact.relative_path).parts
    return parts[0] if parts else artifact.kind


def _learning_note_body(card_markdown: str, summary: LearningNoteSummaryResult) -> str:
    return f"# {summary.title}\n\n{card_markdown.strip()}\n"


def _learning_note_inbox_entry(note: str, timestamp: datetime) -> str:
    return (
        f"## {timestamp.strftime('%H:%M:%S')} 学习输入\n\n"
        f"{note.strip()}\n"
    )


def _real_interview_record_body(
    record: str,
    questions: list[str],
    weak_points: list[str],
) -> str:
    return (
        "# 真实面试记录\n\n"
        "## 原始记录\n\n"
        f"{record.strip()}\n\n"
        "## 抽取问题\n\n"
        f"{_plain_bullet_list(questions)}\n\n"
        "## 薄弱线索\n\n"
        f"{_plain_bullet_list(weak_points)}\n"
    )


def _create_or_merge_high_frequency_card(
    artifact_service,
    relative_path: str,
    *,
    questions: list[str],
    weak_points: list[str],
    language: str,
    source_ref: str,
    timestamp: datetime,
) -> None:
    try:
        current = artifact_service.read_markdown(relative_path)
        sections = _markdown_sections(current.body)
        existing_questions = _markdown_list_items(sections.get("真实面试高频问题") or "")
        existing_weak_points = _markdown_list_items(sections.get("暴露问题") or "")
        source_refs = _unique_card_items([*current.front_matter.source_refs, source_ref])
    except FileNotFoundError:
        current = None
        existing_questions = []
        existing_weak_points = []
        source_refs = [source_ref]

    merged_questions = _unique_card_items([*existing_questions, *questions])
    merged_weak_points = _unique_card_items([*existing_weak_points, *weak_points])
    body = (
        "# 高频与薄弱点\n\n"
        "## 真实面试高频问题\n\n"
        f"{_plain_bullet_list(merged_questions)}\n\n"
        "## 暴露问题\n\n"
        f"{_plain_bullet_list(merged_weak_points)}\n"
    )
    if current is None:
        artifact_service.create_markdown(
            relative_path,
            kind="high_frequency",
            language=language,
            body=body,
            source_refs=source_refs,
            origin="llm",
            edited_by="system",
            now=timestamp,
        )
        return
    artifact_service.replace_body(
        relative_path,
        expected_revision=current.front_matter.revision,
        body=body,
        edited_by="system",
        source_refs=source_refs,
        now=timestamp,
    )


def _create_or_merge_review_status_from_real_interview(
    artifact_service,
    relative_path: str,
    *,
    questions: list[str],
    weak_points: list[str],
    language: str,
    evidence_ref: str,
    timestamp: datetime,
) -> None:
    tasks = _real_interview_focus_items(questions, weak_points)
    try:
        current = artifact_service.read_markdown(relative_path)
        sections = _markdown_sections(current.body)
        recent_learning = _markdown_list_items(sections.get("最近整理") or "")
        recent_practice = _markdown_list_items(sections.get("最近练习") or "")
    except FileNotFoundError:
        current = None
        recent_learning = []
        recent_practice = []

    body = (
        "# 复习状态\n\n"
        "## 当前重点\n\n"
        f"{_plain_bullet_list(tasks)}\n\n"
        "## 最近整理\n\n"
        f"{_plain_bullet_list(recent_learning)}\n\n"
        "## 最近练习\n\n"
        f"{_plain_bullet_list(recent_practice)}\n"
    )
    if current is None:
        artifact_service.create_markdown(
            relative_path,
            kind="review_status",
            language=language,
            body=body,
            evidence_refs=[evidence_ref],
            origin="llm",
            edited_by="system",
            now=timestamp,
        )
        return
    artifact_service.replace_body(
        relative_path,
        expected_revision=current.front_matter.revision,
        body=body,
        edited_by="system",
        now=timestamp,
    )


def _extract_real_interview_questions(record: str) -> list[str]:
    questions: list[str] = []
    for raw_line in record.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        interviewer = re.match(r"^(?:面试官|interviewer|问)[:：]\s*(.+)$", line, re.IGNORECASE)
        if interviewer:
            candidate = interviewer.group(1).strip()
        elif re.search(r"[?？]\s*$", line) and not re.match(r"^(?:我|候选人|answer)[:：]", line):
            candidate = re.sub(r"^[^:：]{1,12}[:：]\s*", "", line).strip()
        else:
            continue
        if candidate and not re.search(r"[?？]\s*$", candidate):
            candidate = f"{candidate}？"
        questions.append(candidate)
    return _unique_card_items(questions)


def _extract_real_interview_weak_points(record: str) -> list[str]:
    markers = ("答差", "不会", "没答好", "没答出来", "卡住", "薄弱", "不熟", "忘了", "没说清", "答得不好")
    weak_points: list[str] = []
    for raw_line in record.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(marker in line for marker in markers):
            weak_points.append(line)
    return _unique_card_items(weak_points)


def _real_interview_focus_items(questions: list[str], weak_points: list[str]) -> list[str]:
    tasks: list[str] = []
    if questions:
        tasks.append(f"复盘真实面试题：{questions[0]}")
    if weak_points:
        tasks.append(f"补齐薄弱点：{weak_points[0]}")
    for question in questions[1:]:
        tasks.append(f"准备标准说法：{question}")
    for weak_point in weak_points[1:]:
        tasks.append(f"纠正真实面试暴露问题：{weak_point}")
    if not tasks:
        tasks.append("整理真实面试记录，补全问题和回答。")
    return tasks[:3]


def _plain_bullet_list(items: list[str]) -> str:
    compact = _unique_card_items(items)
    if not compact:
        return "- 暂无。"
    return "\n".join(f"- {item}" for item in compact)


def _learning_note_card(note: str, summary: LearningNoteSummaryResult) -> str:
    correction_items = _unique_card_items([summary.summary, *summary.key_points])
    interview_items = summary.interview_takeaways or [summary.summary]
    follow_up_items = summary.follow_up_questions[:3] or ["这个知识点在真实项目中如何落地？"]
    return (
        "- 我的理解：\n"
        f"{_indented_card_text(note)}\n"
        "- 修正/补充：\n"
        f"{_card_list(correction_items)}\n"
        "- 30 秒面试说法：\n"
        f"{_card_list(interview_items)}\n"
        "- 易混点：\n"
        "  - 暂无明确易混点，后续练习中补充。\n"
        "- 追问：\n"
        f"{_card_list(follow_up_items)}\n"
    )


def _create_or_merge_learning_card(
    artifact_service,
    knowledge_path: str,
    *,
    card_markdown: str,
    summary: LearningNoteSummaryResult,
    language: str,
    source_ref: str,
    timestamp: datetime,
) -> None:
    try:
        current = artifact_service.read_markdown(knowledge_path)
    except FileNotFoundError:
        artifact_service.create_markdown(
            knowledge_path,
            kind="knowledge",
            language=language,
            body=_learning_note_body(card_markdown, summary),
            source_refs=[source_ref],
            origin="llm",
            edited_by="system",
            now=timestamp,
        )
        return
    merged_body = f"{current.body.rstrip()}\n\n---\n\n{card_markdown.strip()}\n"
    artifact_service.replace_body(
        knowledge_path,
        expected_revision=current.front_matter.revision,
        body=merged_body,
        edited_by="system",
        source_refs=_unique_card_items([*current.front_matter.source_refs, source_ref]),
        now=timestamp,
    )


def _create_or_merge_review_status_from_learning(
    artifact_service,
    relative_path: str,
    *,
    title: str,
    source_ref: str,
    language: str,
    timestamp: datetime,
) -> None:
    line = f"整理知识卡：{title.strip()}"
    try:
        current = artifact_service.read_markdown(relative_path)
        sections = _markdown_sections(current.body)
        recent_items = _markdown_list_items(sections.get("最近整理") or "")
        source_refs = _unique_card_items([*current.front_matter.source_refs, source_ref])
    except FileNotFoundError:
        current = None
        recent_items = []
        source_refs = [source_ref]

    recent = _unique_card_items([line, *recent_items])[:8]
    body = (
        "# 复习状态\n\n"
        "## 当前重点\n\n"
        "- 通过模拟面试暴露薄弱点后自动更新。\n\n"
        "## 最近整理\n\n"
        f"{_plain_bullet_list(recent)}\n"
    )
    if current is None:
        artifact_service.create_markdown(
            relative_path,
            kind="review_status",
            language=language,
            body=body,
            source_refs=source_refs,
            origin="llm",
            edited_by="system",
            now=timestamp,
        )
        return
    artifact_service.replace_body(
        relative_path,
        expected_revision=current.front_matter.revision,
        body=body,
        edited_by="system",
        source_refs=source_refs,
        now=timestamp,
    )


def _indented_card_text(value: str) -> str:
    lines = value.strip().splitlines() or ["（空内容）"]
    return "\n".join(f"  {line}" if line.strip() else "" for line in lines)


def _card_list(items: list[str]) -> str:
    compact = _unique_card_items(items)
    if not compact:
        return "  - 暂无明确内容，后续练习中补充。"
    return "\n".join(f"  - {item}" for item in compact[:3])


def _unique_card_items(items: list[str]) -> list[str]:
    compact: list[str] = []
    seen: set[str] = set()
    for item in items:
        stripped = item.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        compact.append(stripped)
    return compact


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", value).strip("-").lower()
    return slug[:80] or "learning-note"


def _sse_event(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _http_error_payload(error: HTTPException) -> dict[str, object]:
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


def _parse_learning_note_summary(
    markdown: str,
    note: str,
    language: str,
) -> LearningNoteSummaryResult:
    title_match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    fallback_title = "学习记录" if language == "zh-CN" else "Learning note"
    title = title_match.group(1).strip()[:80] if title_match else _slug(note).replace("-", " ")
    sections = _markdown_sections(markdown)
    summary = (
        sections.get("summary")
        or sections.get("摘要")
        or sections.get("ai 整理摘要")
        or note[:240]
        or fallback_title
    )
    key_points = _markdown_list_items(
        sections.get("key points") or sections.get("关键点") or ""
    )
    interview_takeaways = _markdown_list_items(
        sections.get("interview expression") or sections.get("面试表达") or ""
    )
    follow_up_questions = _markdown_list_items(
        sections.get("follow-up questions") or sections.get("可追问问题") or ""
    )
    return LearningNoteSummaryResult(
        title=title or fallback_title,
        summary=summary.strip(),
        key_points=key_points,
        interview_takeaways=interview_takeaways,
        follow_up_questions=follow_up_questions,
    )


def _markdown_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        heading = re.match(r"^##\s+(.+)$", line.strip())
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _markdown_list_items(value: str) -> list[str]:
    items: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", stripped).strip()
        if stripped:
            items.append(stripped)
    return items
