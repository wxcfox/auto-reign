from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.repositories.artifact_repository import ArtifactRepository
from app.services.artifact_service import ArtifactService, ManagedMarkdown
from app.services.content_renderer import (
    labels_for,
    render_high_frequency,
    render_practice_session,
    render_question_bank,
    render_review_status,
    section_items,
)
from app.services.markdown_utils import (
    markdown_sections,
    plain_bullet_list,
    replace_or_append_h2,
    slugify,
    unique_items,
)
from app.schemas.modeling import AnswerEvaluationResult
from app.services.workspace_service import WorkspaceService
from app.services.workspace_paths import HIGH_FREQUENCY_PATH, MASTERY_PATH, REVIEW_STATUS_PATH


PROJECT_CONTEXT_LIMIT = 3


@dataclass(frozen=True)
class ArchivedInterviewArtifacts:
    report_path: str
    report: ManagedMarkdown


class InterviewArtifactService:
    def __init__(
        self,
        *,
        user_id: int,
        settings: Settings | None = None,
        workspace_service: WorkspaceService | None = None,
        artifact_service: ArtifactService | None = None,
        artifact_repository: ArtifactRepository | None = None,
    ) -> None:
        self.user_id = user_id
        self.settings = settings or get_settings()
        self.workspace = workspace_service or WorkspaceService(
            self.settings.data_dir / "users" / str(user_id) / "workspace",
            default_manifest_path=self.settings.default_manifest_path,
        )
        self.workspace.initialize()
        self.artifacts = artifact_service or ArtifactService(self.workspace)
        self.repository = artifact_repository or ArtifactRepository()

    def record_answer_evaluation(
        self,
        session: Session,
        config: Any,
        turn: Any,
        *,
        question: str,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        self.upsert_question_bank_entry(session, config, turn, question=question, evaluation=evaluation)
        self.upsert_high_frequency_question(config, question, evaluation)

    def record_answer_progress(
        self,
        session: Session,
        interview_session: Any,
        config: Any,
        turns: list[Any],
    ) -> None:
        if not any(turn.answer or turn.follow_up_answer for turn in turns):
            return
        evidence_ref = f"interview_session:{interview_session.id}"
        self.write_practice(interview_session, config, turns, evidence_ref=evidence_ref)
        self.upsert_review_status(config, turns, evidence_ref=evidence_ref)
        self.workspace.rebuild_projection(
            session,
            self.repository,
            self.artifacts,
            user_id=self.user_id,
        )

    def archive_finished_session(
        self,
        session: Session,
        interview_session: Any,
        config: Any,
        turns: list[Any],
        report_markdown: str,
    ) -> ArchivedInterviewArtifacts:
        evidence_ref = f"interview_session:{interview_session.id}"
        practice = self.write_practice(interview_session, config, turns, evidence_ref=evidence_ref)
        practice_ref = f"practice:{practice.front_matter.id}"
        self.write_mastery(turns, config.language or "zh-CN", practice_ref)
        self.upsert_review_status(config, turns, evidence_ref=practice_ref)
        report_path, report = self.write_report(
            interview_session,
            report_markdown,
            config.language or "zh-CN",
            practice_ref,
        )
        self.workspace.rebuild_projection(
            session,
            self.repository,
            self.artifacts,
            user_id=self.user_id,
        )
        return ArchivedInterviewArtifacts(report_path=report_path, report=report)

    def write_practice(
        self,
        interview_session: Any,
        config: Any,
        turns: list[Any],
        *,
        evidence_ref: str,
    ) -> ManagedMarkdown:
        language = config.language or "zh-CN"
        labels = labels_for(language)
        started = interview_session.started_at.astimezone(UTC)
        practice_path = f"practice/{started:%Y-%m-%d}.md"
        session_heading = f"{labels.session} {interview_session.id}"
        session_body = self.practice_session_body(interview_session, config, turns)
        try:
            current = self.artifacts.read_markdown(practice_path)
        except FileNotFoundError:
            return self.artifacts.create_markdown(
                practice_path,
                kind="practice",
                body=f"# {labels.practice_title}\n\n## {session_heading}\n\n{session_body}\n",
                language=language,
                origin="observed",
                edited_by="system",
                evidence_refs=[evidence_ref],
            )
        body = replace_or_append_h2(current.body, session_heading, session_body)
        return self.artifacts.replace_body(
            practice_path,
            expected_revision=current.front_matter.revision,
            body=body,
            edited_by="system",
            now=datetime.now(UTC),
        )

    def practice_session_body(
        self,
        interview_session: Any,
        config: Any,
        turns: list[Any],
    ) -> str:
        return render_practice_session(
            interview_session,
            config,
            turns,
            config.language or "zh-CN",
        )

    def upsert_review_status(
        self,
        config: Any,
        turns: list[Any],
        *,
        evidence_ref: str,
    ) -> None:
        status_path = REVIEW_STATUS_PATH
        focus = unique_items(
            [
                item
                for turn in turns
                for item in [
                    *turn.weaknesses,
                    *turn.follow_up_weaknesses,
                    *turn.missing_points,
                    *turn.follow_up_missing_points,
                    *turn.review_suggestions,
                    *turn.follow_up_review_suggestions,
                ]
            ],
            limit=3,
        )
        latest_question = next((turn.question for turn in reversed(turns) if turn.answer), "")
        language = config.language or "zh-CN"
        labels = labels_for(language)
        latest_practice = (
            f"{labels.practice_prefix}{labels.colon}{latest_question}" if latest_question else ""
        )
        try:
            current = self.artifacts.read_markdown(status_path)
            sections = markdown_sections(current.body)
            recent_learning = section_items(sections, language, "recent_learning")
            recent_practice = section_items(sections, language, "recent_practice")
            evidence_refs = unique_items([*current.front_matter.evidence_refs, evidence_ref], limit=20)
        except FileNotFoundError:
            current = None
            recent_learning = []
            recent_practice = []
            evidence_refs = [evidence_ref]

        practice_items = unique_items([latest_practice, *recent_practice], limit=8)
        body = render_review_status(
            focus or [labels.continue_practice],
            recent_learning,
            practice_items,
            language,
        )
        if current is None:
            self.artifacts.create_markdown(
                status_path,
                kind="review_status",
                body=body,
            language=language,
                origin="llm",
                edited_by="system",
                evidence_refs=evidence_refs,
            )
            return
        self.artifacts.replace_body(
            status_path,
            expected_revision=current.front_matter.revision,
            body=body,
            edited_by="system",
            now=datetime.now(UTC),
        )

    def upsert_high_frequency_question(
        self,
        config: Any,
        question: str | None,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        if not evaluation.should_write_high_frequency or not question:
            return
        relative_path = HIGH_FREQUENCY_PATH
        try:
            current = self.artifacts.read_markdown(relative_path)
            sections = markdown_sections(current.body)
            language = config.language or "zh-CN"
            existing_questions = section_items(sections, language, "real_interview_questions")
            existing_weak_points = section_items(sections, language, "exposed_issues")
        except FileNotFoundError:
            current = None
            existing_questions = []
            existing_weak_points = []

        questions = unique_items([question, *existing_questions], limit=20)
        weak_points = unique_items(
            [*evaluation.weaknesses, *evaluation.missing_points, *existing_weak_points],
            limit=20,
        )
        language = config.language or "zh-CN"
        body = render_high_frequency(questions, weak_points, language)
        if current is None:
            self.artifacts.create_markdown(
                relative_path,
                kind="high_frequency",
                body=body,
                language=language,
                origin="llm",
                edited_by="system",
            )
            return
        self.artifacts.replace_body(
            relative_path,
            expected_revision=current.front_matter.revision,
            body=body,
            edited_by="system",
            now=datetime.now(UTC),
        )

    def upsert_question_bank_entry(
        self,
        session: Session,
        config: Any,
        turn: Any,
        *,
        question: str,
        evaluation: AnswerEvaluationResult,
    ) -> None:
        if not (
            evaluation.should_write_weakness
            or evaluation.missing_points
            or evaluation.weaknesses
        ):
            return

        relative_path = self.question_bank_path(question)
        body = self.question_bank_body(session, config, question, evaluation)
        existing = self.repository.get_by_relative_path(
            session,
            user_id=self.user_id,
            relative_path=relative_path,
        )
        path_exists = self.workspace.resolve_path(relative_path).exists()
        if existing is not None or path_exists:
            current = self.artifacts.read_markdown(relative_path)
            self.artifacts.replace_body(
                relative_path,
                expected_revision=current.front_matter.revision,
                body=body,
                edited_by="system",
            )
        else:
            self.artifacts.create_markdown(
                relative_path,
                kind="question_bank",
                body=body,
                language=config.language,
                evidence_refs=[f"interview_turn:{turn.id}"],
                origin="llm",
                edited_by="system",
            )
        self.workspace.rebuild_projection(
            session,
            self.repository,
            self.artifacts,
            user_id=self.user_id,
        )

    def question_bank_path(self, question: str) -> str:
        digest = hashlib.sha1(question.strip().encode("utf-8")).hexdigest()[:10]
        slug = slugify(question, fallback="question", max_length=60)
        return f"questions/{slug}-{digest}.md"

    def question_bank_body(
        self,
        session: Session,
        config: Any,
        question: str,
        evaluation: AnswerEvaluationResult,
    ) -> str:
        tested_points = evaluation.tested_points or [config.target_role, config.job_description]
        error_points = [*evaluation.missing_points, *evaluation.weaknesses]
        project_lines = [
            item.split("\n", 1)[1].strip()
            for item in self.project_context(session)
            if "\n" in item and item.split("\n", 1)[1].strip()
        ]
        language = config.language or "zh-CN"
        labels = labels_for(language)
        project_section = (
            "\n\n".join(project_lines)
            if project_lines
            else labels.project_fallback
        )
        review_status = evaluation.mastery_change or "weak"
        if evaluation.should_write_weakness:
            review_status = (
                review_status
                if "weak" in review_status
                else f"{review_status}{labels.clause_separator}{labels.write_weakness}"
            )
        if evaluation.should_write_high_frequency:
            review_status = (
                f"{review_status}{labels.clause_separator}{labels.write_high_frequency}"
            )

        return render_question_bank(
            question,
            tested_points,
            evaluation.better_answer or evaluation.feedback,
            project_section,
            evaluation.follow_up_question,
            error_points or evaluation.review_suggestions,
            review_status,
            language,
        )

    def write_mastery(self, turns: list[Any], language: str, evidence_ref: str) -> None:
        weaknesses = unique_items(
            [item for turn in turns for item in [*turn.weaknesses, *turn.follow_up_weaknesses]],
            limit=6,
        )
        if language == "zh-CN":
            body = "# 掌握状态\n\n## 需要加强\n\n" + plain_bullet_list(
                weaknesses or ["继续积累练习证据"]
            )
        else:
            body = "# Mastery State\n\n## Needs Work\n\n" + plain_bullet_list(
                weaknesses or ["Keep collecting practice evidence"]
            )
        self.upsert_markdown(MASTERY_PATH, "mastery", body, language, [evidence_ref])

    def write_report(
        self,
        interview_session: Any,
        report_markdown: str,
        language: str,
        evidence_ref: str,
    ) -> tuple[str, ManagedMarkdown]:
        now = datetime.now(UTC)
        relative_path = f"reports/{now:%Y-%m-%d}-{interview_session.id}.md"
        report = self.artifacts.create_markdown(
            relative_path,
            kind="report",
            body=report_markdown,
            language=language,
            origin="llm",
            evidence_refs=[evidence_ref],
        )
        return relative_path, report

    def upsert_markdown(
        self,
        relative_path: str,
        kind: str,
        body: str,
        language: str,
        evidence_refs: list[str],
    ) -> ManagedMarkdown:
        path = self.workspace.resolve_path(relative_path)
        if path.exists():
            current = self.artifacts.read_markdown(relative_path)
            return self.artifacts.replace_body(
                relative_path,
                expected_revision=current.front_matter.revision,
                body=body,
                edited_by="system",
            )
        return self.artifacts.create_markdown(
            relative_path,
            kind=kind,  # type: ignore[arg-type]
            body=body,
            language=language,
            origin="llm",
            evidence_refs=evidence_refs,
        )

    def project_context(self, session: Session) -> list[str]:
        context: list[str] = []
        project_artifacts = [
            artifact
            for artifact in self.repository.list(session, user_id=self.user_id)
            if artifact.kind == "project"
        ][:PROJECT_CONTEXT_LIMIT]
        for artifact in project_artifacts:
            try:
                body = self.artifacts.read_markdown(artifact.relative_path).body.strip()
            except Exception:
                continue
            if body:
                context.append(f"[项目材料 | {artifact.relative_path}]\n{body}")
        return context
