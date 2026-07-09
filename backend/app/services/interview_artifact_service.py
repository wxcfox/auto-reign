from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.repositories.artifact_repository import ArtifactRepository
from app.services.artifact_service import ArtifactService, ManagedMarkdown
from app.services.markdown_utils import (
    markdown_list_items,
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
        started = interview_session.started_at.astimezone(UTC)
        practice_path = f"practice/{started:%Y-%m-%d}.md"
        session_heading = f"会话 {interview_session.id}"
        session_body = self.practice_session_body(interview_session, config, turns)
        try:
            current = self.artifacts.read_markdown(practice_path)
        except FileNotFoundError:
            return self.artifacts.create_markdown(
                practice_path,
                kind="practice",
                body=f"# 模拟面试记录\n\n## {session_heading}\n\n{session_body}\n",
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
        body = [
            f"- 开始时间：{interview_session.started_at.astimezone(UTC).isoformat().replace('+00:00', 'Z')}",
            f"- 出题要求：{config.extra_prompt.strip() or '默认抽检'}",
            "",
        ]
        for turn in turns:
            body.extend(
                [
                    f"### 第 {turn.round_index} 轮",
                    "",
                    f"**问题**：{turn.question}",
                    "",
                    f"**回答**：{turn.answer or ''}",
                    "",
                    f"**点评**：{turn.feedback or ''}",
                    "",
                ]
            )
            if turn.missing_points:
                body.extend(["**缺失点**：", plain_bullet_list(turn.missing_points), ""])
            if turn.weaknesses:
                body.extend(["**薄弱点**：", plain_bullet_list(turn.weaknesses), ""])
            if turn.review_suggestions:
                body.extend(["**复习建议**：", plain_bullet_list(turn.review_suggestions), ""])
            if turn.better_answer:
                body.extend(["**更好的面试说法**：", turn.better_answer, ""])
            if turn.tested_points:
                body.extend(["**本题考察点**：", plain_bullet_list(turn.tested_points), ""])
            if turn.mastery_change and turn.mastery_change != "unchanged":
                body.extend(["**掌握状态变化**：", turn.mastery_change, ""])
            if turn.follow_up_question:
                body.extend(
                    [
                        f"**追问**：{turn.follow_up_question}",
                        "",
                        f"**追问回答**：{turn.follow_up_answer or ''}",
                        "",
                        f"**追问点评**：{turn.follow_up_feedback or ''}",
                        "",
                    ]
                )
                if turn.follow_up_missing_points:
                    body.extend(["**追问缺失点**：", plain_bullet_list(turn.follow_up_missing_points), ""])
                if turn.follow_up_weaknesses:
                    body.extend(["**追问薄弱点**：", plain_bullet_list(turn.follow_up_weaknesses), ""])
                if turn.follow_up_review_suggestions:
                    body.extend(["**追问复习建议**：", plain_bullet_list(turn.follow_up_review_suggestions), ""])
                if turn.follow_up_better_answer:
                    body.extend(["**追问更好的面试说法**：", turn.follow_up_better_answer, ""])
                if turn.follow_up_tested_points:
                    body.extend(["**追问考察点**：", plain_bullet_list(turn.follow_up_tested_points), ""])
                if (
                    turn.follow_up_mastery_change
                    and turn.follow_up_mastery_change != "unchanged"
                ):
                    body.extend(["**追问掌握状态变化**：", turn.follow_up_mastery_change, ""])
                follow_up_write_suggestions = []
                if turn.follow_up_should_write_weakness:
                    follow_up_write_suggestions.append("写入薄弱点")
                if turn.follow_up_should_write_high_frequency:
                    follow_up_write_suggestions.append("写入高频题")
                if follow_up_write_suggestions:
                    body.extend(
                        [
                            "**追问写入建议**：",
                            plain_bullet_list(follow_up_write_suggestions),
                            "",
                        ]
                    )
        return "\n".join(body).strip()

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
        latest_practice = f"练习：{latest_question}" if latest_question else ""
        try:
            current = self.artifacts.read_markdown(status_path)
            sections = markdown_sections(current.body)
            recent_learning = markdown_list_items(sections.get("最近整理") or "", unique=True, limit=100)
            recent_practice = markdown_list_items(sections.get("最近练习") or "", unique=True, limit=100)
            evidence_refs = unique_items([*current.front_matter.evidence_refs, evidence_ref], limit=20)
        except FileNotFoundError:
            current = None
            recent_learning = []
            recent_practice = []
            evidence_refs = [evidence_ref]

        practice_items = unique_items([latest_practice, *recent_practice], limit=8)
        recent_learning_text = plain_bullet_list(recent_learning) if recent_learning else "暂无。"
        recent_practice_text = plain_bullet_list(practice_items) if practice_items else "暂无。"
        body = (
            "# 复习状态\n\n"
            "## 当前重点\n\n"
            f"{plain_bullet_list(focus or ['继续通过模拟面试暴露薄弱点'])}\n\n"
            "## 最近整理\n\n"
            f"{recent_learning_text}\n\n"
            "## 最近练习\n\n"
            f"{recent_practice_text}\n"
        )
        if current is None:
            self.artifacts.create_markdown(
                status_path,
                kind="review_status",
                body=body,
                language=config.language or "zh-CN",
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
            existing_questions = markdown_list_items(
                sections.get("真实面试高频问题") or "", unique=True, limit=100
            )
            existing_weak_points = markdown_list_items(
                sections.get("暴露问题") or "", unique=True, limit=100
            )
        except FileNotFoundError:
            current = None
            existing_questions = []
            existing_weak_points = []

        questions = unique_items([question, *existing_questions], limit=20)
        weak_points = unique_items(
            [*evaluation.weaknesses, *evaluation.missing_points, *existing_weak_points],
            limit=20,
        )
        body = (
            "# 高频与薄弱点\n\n"
            "## 真实面试高频问题\n\n"
            f"{plain_bullet_list(questions)}\n\n"
            "## 暴露问题\n\n"
            f"{plain_bullet_list(weak_points)}\n"
        )
        if current is None:
            self.artifacts.create_markdown(
                relative_path,
                kind="high_frequency",
                body=body,
                language=config.language or "zh-CN",
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
        project_section = (
            "\n\n".join(project_lines)
            if project_lines
            else "结合已有项目材料补充业务场景、角色职责、技术取舍和结果指标。"
        )
        review_status = evaluation.mastery_change or "weak"
        if evaluation.should_write_weakness:
            review_status = review_status if "weak" in review_status else f"{review_status}；写入薄弱点"
        if evaluation.should_write_high_frequency:
            review_status = f"{review_status}；写入高频题"

        return (
            f"## 问题：{question.strip()}\n\n"
            "### 考察点\n\n"
            f"{plain_bullet_list(tested_points)}\n\n"
            "### 标准回答\n\n"
            f"{(evaluation.better_answer or evaluation.feedback).strip()}\n\n"
            "### 结合项目\n\n"
            f"{project_section}\n\n"
            "### 常见追问\n\n"
            f"{evaluation.follow_up_question.strip() or '暂无。'}\n\n"
            "### 易错点\n\n"
            f"{plain_bullet_list(error_points or evaluation.review_suggestions)}\n\n"
            "### 复习状态\n\n"
            f"{review_status}\n"
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
