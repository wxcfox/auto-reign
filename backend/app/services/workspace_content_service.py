from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.db import models
from app.db.session import session_scope
from app.repositories.artifact_repository import ArtifactRepository
from app.services.artifact_service import ArtifactService
from app.services.content_renderer import (
    labels_for,
    real_interview_focus_items,
    render_high_frequency,
    render_learning_inbox_entry,
    render_learning_note_card,
    render_real_interview_record,
    render_review_status,
    section_items,
)
from app.services.markdown_utils import (
    markdown_sections,
    slugify,
    unique_items,
)
from app.schemas.modeling import LearningNoteSummaryResult
from app.services.workspace_service import WorkspaceService
from app.services.workspace_paths import (
    HIGH_FREQUENCY_PATH,
    RAW_SOURCE_DIR,
    REVIEW_STATUS_PATH,
)


@dataclass(frozen=True)
class UploadedSourceRef:
    artifact_id: str
    relative_path: str
    duplicate: bool


@dataclass(frozen=True)
class LearningNotePersistenceResult:
    source: UploadedSourceRef
    artifact: models.Artifact
    summary: LearningNoteSummaryResult
    card_markdown: str


@dataclass(frozen=True)
class RealInterviewRecordPersistenceResult:
    raw_artifact: models.Artifact
    high_frequency_artifact: models.Artifact
    status_artifact: models.Artifact
    questions: list[str]
    weak_points: list[str]


class WorkspaceContentProjectionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WorkspaceContentService:
    def __init__(
        self,
        *,
        user_id: int,
        workspace_service: WorkspaceService,
        artifact_service: ArtifactService,
        session_factory: sessionmaker[Session],
        repository: ArtifactRepository | None = None,
    ) -> None:
        self.user_id = user_id
        self.workspace_service = workspace_service
        self.artifact_service = artifact_service
        self.session_factory = session_factory
        self.repository = repository or ArtifactRepository()

    def persist_learning_note(
        self,
        note: str,
        language: str,
        summary: LearningNoteSummaryResult,
    ) -> LearningNotePersistenceResult:
        timestamp = datetime.now(UTC)
        source = self.artifact_service.append_source(
            f"{RAW_SOURCE_DIR}/{timestamp.strftime('%Y-%m-%d')}-learning-notes.md",
            source_filename=f"{timestamp.strftime('%Y-%m-%d')}.md",
            media_type="text/markdown",
            content=self.learning_note_inbox_entry(note, timestamp, language).encode("utf-8"),
            language=language,
            uploaded_at=timestamp,
            source_type="learning_note",
        )
        source_ref = f"source:{source.artifact_id}"
        knowledge_path = (
            f"knowledge/{slugify(summary.title, fallback='learning-note', max_length=80)}.md"
        )
        card_markdown = self.learning_note_card(note, summary, language)
        self.create_or_merge_learning_card(
            knowledge_path,
            card_markdown=card_markdown,
            summary=summary,
            language=language,
            source_ref=source_ref,
            timestamp=timestamp,
        )
        self.create_or_merge_review_status_from_learning(
            REVIEW_STATUS_PATH,
            title=summary.title,
            source_ref=source_ref,
            language=language,
            timestamp=timestamp,
        )

        with session_scope(self.session_factory) as session:
            self.workspace_service.rebuild_projection(
                session,
                self.repository,
                self.artifact_service,
                user_id=self.user_id,
            )
            source_artifact = self.repository.get(
                session,
                user_id=self.user_id,
                artifact_id=source.artifact_id,
            )
            knowledge_artifact = self.repository.get_by_relative_path(
                session,
                user_id=self.user_id,
                relative_path=knowledge_path,
            )
            if source_artifact is None or knowledge_artifact is None:
                raise WorkspaceContentProjectionError(
                    "learning_note_projection_failed",
                    "Learning note was saved but projection failed.",
                )
            return LearningNotePersistenceResult(
                source=UploadedSourceRef(
                    artifact_id=source_artifact.id,
                    relative_path=source_artifact.relative_path,
                    duplicate=False,
                ),
                artifact=knowledge_artifact,
                summary=summary,
                card_markdown=card_markdown,
            )

    def persist_real_interview_record(
        self,
        record: str,
        language: str,
    ) -> RealInterviewRecordPersistenceResult:
        timestamp = datetime.now(UTC)
        questions = self.extract_real_interview_questions(record)
        weak_points = self.extract_real_interview_weak_points(record)
        raw_path = f"{RAW_SOURCE_DIR}/{timestamp.strftime('%Y%m%d-%H%M%S-%f')}-real-interview.md"
        raw_document = self.artifact_service.create_markdown(
            raw_path,
            kind="interview_record",
            language=language,
            body=self.real_interview_record_body(record, questions, weak_points, language),
            origin="human",
            edited_by="user",
            now=timestamp,
        )
        raw_ref = f"artifact:{raw_document.front_matter.id}"
        high_frequency_path = HIGH_FREQUENCY_PATH
        status_path = REVIEW_STATUS_PATH
        self.create_or_merge_high_frequency_card(
            high_frequency_path,
            questions=questions,
            weak_points=weak_points,
            language=language,
            source_ref=raw_ref,
            timestamp=timestamp,
        )
        self.create_or_merge_review_status_from_real_interview(
            status_path,
            questions=questions,
            weak_points=weak_points,
            language=language,
            evidence_ref=raw_ref,
            timestamp=timestamp,
        )

        with session_scope(self.session_factory) as session:
            self.workspace_service.rebuild_projection(
                session,
                self.repository,
                self.artifact_service,
                user_id=self.user_id,
            )
            raw_artifact = self.repository.get(
                session,
                user_id=self.user_id,
                artifact_id=raw_document.front_matter.id,
            )
            high_frequency_artifact = self.repository.get_by_relative_path(
                session,
                user_id=self.user_id,
                relative_path=high_frequency_path,
            )
            status_artifact = self.repository.get_by_relative_path(
                session,
                user_id=self.user_id,
                relative_path=status_path,
            )
            if raw_artifact is None or high_frequency_artifact is None or status_artifact is None:
                raise WorkspaceContentProjectionError(
                    "real_interview_projection_failed",
                    "Real interview record was saved but projection failed.",
                )
            return RealInterviewRecordPersistenceResult(
                raw_artifact=raw_artifact,
                high_frequency_artifact=high_frequency_artifact,
                status_artifact=status_artifact,
                questions=questions,
                weak_points=weak_points,
            )

    @staticmethod
    def learning_note_inbox_entry(
        note: str,
        timestamp: datetime,
        language: str = "zh-CN",
    ) -> str:
        return render_learning_inbox_entry(note, timestamp.strftime("%H:%M:%S"), language)

    @staticmethod
    def real_interview_record_body(
        record: str,
        questions: list[str],
        weak_points: list[str],
        language: str = "zh-CN",
    ) -> str:
        return render_real_interview_record(record, questions, weak_points, language)

    def create_or_merge_high_frequency_card(
        self,
        relative_path: str,
        *,
        questions: list[str],
        weak_points: list[str],
        language: str,
        source_ref: str,
        timestamp: datetime,
    ) -> None:
        try:
            current = self.artifact_service.read_markdown(relative_path)
            sections = markdown_sections(current.body)
            existing_questions = section_items(sections, language, "real_interview_questions")
            existing_weak_points = section_items(sections, language, "exposed_issues")
            source_refs = unique_items([*current.front_matter.source_refs, source_ref])
        except FileNotFoundError:
            current = None
            existing_questions = []
            existing_weak_points = []
            source_refs = [source_ref]

        merged_questions = unique_items([*existing_questions, *questions])
        merged_weak_points = unique_items([*existing_weak_points, *weak_points])
        body = render_high_frequency(merged_questions, merged_weak_points, language)
        if current is None:
            self.artifact_service.create_markdown(
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
        self.artifact_service.replace_body(
            relative_path,
            expected_revision=current.front_matter.revision,
            body=body,
            edited_by="system",
            source_refs=source_refs,
            now=timestamp,
        )

    def create_or_merge_review_status_from_real_interview(
        self,
        relative_path: str,
        *,
        questions: list[str],
        weak_points: list[str],
        language: str,
        evidence_ref: str,
        timestamp: datetime,
    ) -> None:
        tasks = self.real_interview_focus_items(questions, weak_points, language)
        try:
            current = self.artifact_service.read_markdown(relative_path)
            sections = markdown_sections(current.body)
            recent_learning = section_items(sections, language, "recent_learning")
            recent_practice = section_items(sections, language, "recent_practice")
        except FileNotFoundError:
            current = None
            recent_learning = []
            recent_practice = []

        body = render_review_status(tasks, recent_learning, recent_practice, language)
        if current is None:
            self.artifact_service.create_markdown(
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
        self.artifact_service.replace_body(
            relative_path,
            expected_revision=current.front_matter.revision,
            body=body,
            edited_by="system",
            now=timestamp,
        )

    @staticmethod
    def extract_real_interview_questions(record: str) -> list[str]:
        questions: list[str] = []
        for raw_line in record.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            interviewer = re.match(r"^(?:面试官|interviewer|问)[:：]\s*(.+)$", line, re.IGNORECASE)
            if interviewer:
                candidate = interviewer.group(1).strip()
            elif re.search(r"[?？]\s*$", line) and not re.match(
                r"^(?:我|候选人|answer)[:：]", line
            ):
                candidate = re.sub(r"^[^:：]{1,12}[:：]\s*", "", line).strip()
            else:
                continue
            if candidate and not re.search(r"[?？]\s*$", candidate):
                candidate = f"{candidate}？"
            questions.append(candidate)
        return unique_items(questions)

    @staticmethod
    def extract_real_interview_weak_points(record: str) -> list[str]:
        markers = ("答差", "不会", "没答好", "没答出来", "卡住", "薄弱", "不熟", "忘了", "没说清", "答得不好")
        weak_points: list[str] = []
        for raw_line in record.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if any(marker in line for marker in markers):
                weak_points.append(line)
        return unique_items(weak_points)

    @staticmethod
    def real_interview_focus_items(
        questions: list[str],
        weak_points: list[str],
        language: str = "zh-CN",
    ) -> list[str]:
        return real_interview_focus_items(questions, weak_points, language)

    @staticmethod
    def learning_note_card(
        note: str,
        summary: LearningNoteSummaryResult,
        language: str = "zh-CN",
    ) -> str:
        return render_learning_note_card(note, summary, language)

    def create_or_merge_learning_card(
        self,
        knowledge_path: str,
        *,
        card_markdown: str,
        summary: LearningNoteSummaryResult,
        language: str,
        source_ref: str,
        timestamp: datetime,
    ) -> None:
        try:
            current = self.artifact_service.read_markdown(knowledge_path)
        except FileNotFoundError:
            self.artifact_service.create_markdown(
                knowledge_path,
                kind="knowledge",
                language=language,
                body=self.learning_note_body(card_markdown, summary),
                source_refs=[source_ref],
                origin="llm",
                edited_by="system",
                now=timestamp,
            )
            return
        merged_body = f"{current.body.rstrip()}\n\n---\n\n{card_markdown.strip()}\n"
        self.artifact_service.replace_body(
            knowledge_path,
            expected_revision=current.front_matter.revision,
            body=merged_body,
            edited_by="system",
            source_refs=unique_items([*current.front_matter.source_refs, source_ref]),
            now=timestamp,
        )

    def create_or_merge_review_status_from_learning(
        self,
        relative_path: str,
        *,
        title: str,
        source_ref: str,
        language: str,
        timestamp: datetime,
    ) -> None:
        labels = labels_for(language)
        line = f"{labels.organize_card}{labels.colon}{title.strip()}"
        try:
            current = self.artifact_service.read_markdown(relative_path)
            sections = markdown_sections(current.body)
            recent_items = section_items(sections, language, "recent_learning")
            source_refs = unique_items([*current.front_matter.source_refs, source_ref])
        except FileNotFoundError:
            current = None
            recent_items = []
            source_refs = [source_ref]

        recent = unique_items([line, *recent_items])[:8]
        body = render_review_status([labels.auto_focus], recent, [], language)
        if current is None:
            self.artifact_service.create_markdown(
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
        self.artifact_service.replace_body(
            relative_path,
            expected_revision=current.front_matter.revision,
            body=body,
            edited_by="system",
            source_refs=source_refs,
            now=timestamp,
        )

    @staticmethod
    def learning_note_body(
        card_markdown: str,
        summary: LearningNoteSummaryResult,
    ) -> str:
        return f"# {summary.title}\n\n{card_markdown.strip()}\n"
