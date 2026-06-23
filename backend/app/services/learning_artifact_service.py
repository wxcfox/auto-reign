from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import InterviewConfig, InterviewSession, InterviewTurn
from app.repositories.artifact_repository import ArtifactRepository
from app.services.artifact_service import ArtifactService
from app.services.workspace_service import WorkspaceService


class LearningArtifactService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.workspace = WorkspaceService(self.settings.data_dir / "workspace")
        self.workspace.initialize()
        self.artifacts = ArtifactService(self.workspace)
        self.repository = ArtifactRepository()

    def archive_finished_session(
        self,
        session: Session,
        interview_session: InterviewSession,
        config: InterviewConfig,
        turns: list[InterviewTurn],
        report_markdown: str,
    ) -> None:
        language = config.language or "zh-CN"
        practice = self._write_practice(interview_session, config, turns, language)
        evidence_ref = f"practice:{practice.front_matter.id}"
        self._write_mastery(turns, language, evidence_ref)
        self._write_review_status(turns, language, evidence_ref)
        self._write_report(interview_session, report_markdown, language, evidence_ref)
        self.workspace.rebuild_projection(session, self.repository, self.artifacts)

    def _write_practice(
        self,
        interview_session: InterviewSession,
        config: InterviewConfig,
        turns: list[InterviewTurn],
        language: str,
    ):
        started = interview_session.started_at.astimezone(UTC)
        relative_path = f"practice/{started:%Y-%m-%d}.md"
        session_heading = (
            f"会话 {interview_session.id}" if language == "zh-CN" else f"Session {interview_session.id}"
        )
        session_body = self._practice_session_body(interview_session, config, turns, language)
        path = self.workspace.resolve_path(relative_path)
        if path.exists():
            current = self.artifacts.read_markdown(relative_path)
            next_body = self._replace_or_append_h2(current.body, session_heading, session_body)
            return self.artifacts.replace_body(
                relative_path,
                expected_revision=current.front_matter.revision,
                body=next_body,
                edited_by="system",
            )
        if language == "zh-CN":
            body = [
                "# 练习记录",
                "",
                f"## {session_heading}",
                "",
                session_body,
            ]
        else:
            body = [
                "# Practice Record",
                "",
                f"## {session_heading}",
                "",
                session_body,
            ]
        return self.artifacts.create_markdown(
            relative_path,
            kind="practice",
            body="\n".join(body).strip() + "\n",
            language=language,
            origin="observed",
            evidence_refs=[],
        )

    def _practice_session_body(
        self,
        interview_session: InterviewSession,
        config: InterviewConfig,
        turns: list[InterviewTurn],
        language: str,
    ) -> str:
        target = self._target_label(config)
        lines: list[str] = []
        if language == "zh-CN":
            lines.extend(
                [
                    f"- 开始时间：{interview_session.started_at.isoformat()}",
                    f"- 目标：{target or '未指定'}",
                    f"- 出题要求：{config.extra_prompt or '未指定'}",
                    "",
                ]
            )
            for turn in turns:
                lines.extend(
                    [
                        f"### 第 {turn.round_index} 题",
                        "",
                        f"问题：{turn.question}",
                        "",
                        f"回答：{turn.answer or ''}",
                        "",
                        f"点评：{turn.feedback or ''}",
                        "",
                    ]
                )
                if turn.follow_up_question:
                    lines.extend(
                        [
                            f"追问：{turn.follow_up_question}",
                            "",
                            f"追问回答：{turn.follow_up_answer or ''}",
                            "",
                            f"追问点评：{turn.follow_up_feedback or ''}",
                            "",
                        ]
                    )
        else:
            lines.extend(
                [
                    f"- Started: {interview_session.started_at.isoformat()}",
                    f"- Target: {target or 'Not specified'}",
                    f"- Prompt: {config.extra_prompt or 'Not specified'}",
                    "",
                ]
            )
            for turn in turns:
                lines.extend(
                    [
                        f"### Question {turn.round_index}",
                        "",
                        f"Question: {turn.question}",
                        "",
                        f"Answer: {turn.answer or ''}",
                        "",
                        f"Feedback: {turn.feedback or ''}",
                        "",
                    ]
                )
        return "\n".join(lines).strip() + "\n"

    def _target_label(self, config: InterviewConfig) -> str:
        structured = " ".join(
            item.strip()
            for item in [config.target_company, config.target_role]
            if item.strip()
        )
        if structured:
            return structured
        return " ".join(config.extra_prompt.split())[:120]

    def _write_mastery(self, turns: list[InterviewTurn], language: str, evidence_ref: str) -> None:
        weaknesses = self._top_items(
            [item for turn in turns for item in [*turn.weaknesses, *turn.follow_up_weaknesses]], 6
        )
        if language == "zh-CN":
            body = "# 掌握状态\n\n## 需要加强\n\n" + self._bullet_list(weaknesses or ["继续积累练习证据"])
        else:
            body = "# Mastery State\n\n## Needs Work\n\n" + self._bullet_list(weaknesses or ["Keep collecting practice evidence"])
        self._upsert_markdown("state/mastery.md", "mastery", body, language, [evidence_ref])

    def _write_review_status(self, turns: list[InterviewTurn], language: str, evidence_ref: str) -> None:
        candidates = self._top_items(
            [item for turn in turns for item in [*turn.review_suggestions, *turn.follow_up_review_suggestions]],
            3,
        )
        if not candidates:
            candidates = ["复盘本次回答并补齐一个薄弱点" if language == "zh-CN" else "Review one weak answer"]
        if language == "zh-CN":
            body = "# 复习状态\n\n## 当前重点\n\n" + self._bullet_list(candidates[:3])
        else:
            body = "# Review Status\n\n## Current Focus\n\n" + self._bullet_list(candidates[:3])
        self._upsert_markdown("review/status.md", "review_status", body, language, [evidence_ref])

    def _write_report(
        self,
        interview_session: InterviewSession,
        report_markdown: str,
        language: str,
        evidence_ref: str,
    ) -> None:
        now = datetime.now(UTC)
        relative_path = f"reports/{now:%Y-%m-%d}-{interview_session.id}.md"
        self.artifacts.create_markdown(
            relative_path,
            kind="report",
            body=report_markdown,
            language=language,
            origin="llm",
            evidence_refs=[evidence_ref],
        )

    def _upsert_markdown(
        self,
        relative_path: str,
        kind: str,
        body: str,
        language: str,
        evidence_refs: list[str],
    ) -> None:
        path = self.workspace.resolve_path(relative_path)
        if path.exists():
            current = self.artifacts.read_markdown(relative_path)
            self.artifacts.replace_body(
                relative_path,
                expected_revision=current.front_matter.revision,
                body=body,
                edited_by="system",
            )
            return
        self.artifacts.create_markdown(
            relative_path,
            kind=kind,  # type: ignore[arg-type]
            body=body,
            language=language,
            origin="llm",
            evidence_refs=evidence_refs,
        )

    def _top_items(self, values: list[str], limit: int) -> list[str]:
        seen: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen[:limit]

    def _bullet_list(self, values: list[str]) -> str:
        return "\n".join(f"- {value}" for value in values) + "\n"

    def _replace_or_append_h2(self, body: str, heading: str, content: str) -> str:
        lines = body.rstrip().splitlines()
        marker = f"## {heading}"
        start = None
        for index, line in enumerate(lines):
            if line.strip() == marker:
                start = index
                break
        replacement = [marker, "", content.rstrip()]
        if start is None:
            return body.rstrip() + "\n\n" + "\n".join(replacement) + "\n"

        end = len(lines)
        for index in range(start + 1, len(lines)):
            if lines[index].startswith("## "):
                end = index
                break
        return "\n".join([*lines[:start], *replacement, *lines[end:]]).rstrip() + "\n"
