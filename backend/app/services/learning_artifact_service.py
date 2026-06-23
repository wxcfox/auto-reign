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
        self._write_plan(turns, language, evidence_ref)
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
        relative_path = (
            f"practice/{started:%Y}/{started:%m}/{started:%Y-%m-%d}-{interview_session.id}.md"
        )
        if language == "zh-CN":
            body = [
                "# 练习记录",
                "",
                f"目标：{self._target_label(config) or '未指定'}",
                "",
            ]
            for turn in turns:
                body.extend(
                    [
                        f"## 第 {turn.round_index} 轮",
                        "",
                        f"### 问题\n\n{turn.question}",
                        f"### 回答\n\n{turn.answer or ''}",
                        f"### 点评\n\n{turn.feedback or ''}",
                    ]
                )
                if turn.follow_up_question:
                    body.extend(
                        [
                            f"### 追问\n\n{turn.follow_up_question}",
                            f"### 追问回答\n\n{turn.follow_up_answer or ''}",
                            f"### 追问点评\n\n{turn.follow_up_feedback or ''}",
                        ]
                    )
        else:
            body = [
                "# Practice Record",
                "",
                f"Target: {self._target_label(config) or 'Not specified'}",
                "",
            ]
            for turn in turns:
                body.extend(
                    [
                        f"## Round {turn.round_index}",
                        "",
                        f"### Question\n\n{turn.question}",
                        f"### Answer\n\n{turn.answer or ''}",
                        f"### Feedback\n\n{turn.feedback or ''}",
                    ]
                )
        return self.artifacts.create_markdown(
            relative_path,
            kind="practice",
            body="\n".join(body).strip() + "\n",
            language=language,
            origin="observed",
            evidence_refs=[],
        )

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

    def _write_plan(self, turns: list[InterviewTurn], language: str, evidence_ref: str) -> None:
        candidates = self._top_items(
            [item for turn in turns for item in [*turn.review_suggestions, *turn.follow_up_review_suggestions]],
            3,
        )
        if not candidates:
            candidates = ["复盘本次回答并补齐一个薄弱点" if language == "zh-CN" else "Review one weak answer"]
        if language == "zh-CN":
            body = "# 当前计划\n\n## 优先任务\n\n" + self._bullet_list(candidates[:3])
        else:
            body = "# Current Plan\n\n## Priorities\n\n" + self._bullet_list(candidates[:3])
        self._upsert_markdown("state/plan.md", "plan", body, language, [evidence_ref])

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
