from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import conflict, not_found
from app.db.models import InterviewConfig, InterviewSession, MemoryFile, Report
from app.repositories.chroma_store import ChromaChunk
from app.repositories.sqlite import (
    InterviewSessionRepository,
    InterviewTurnRepository,
    MemoryFileRepository,
    ReportRepository,
)
from app.services.model_service import MemoryUpdateRequest, ModelService, ReportGenerationRequest
from app.services.rag_service import RagService

MEMORY_LAYOUT = {
    "weakness": {
        "filename": "weakness_memory.md",
        "title": "# Weakness Memory",
        "summary_heading": "## Current Weakness Summary",
        "history_heading": "## Weakness History",
    },
    "interview_history": {
        "filename": "interview_history.md",
        "title": "# Interview History",
        "summary_heading": "## Current Interview Summary",
        "history_heading": "## Interview Records",
    },
    "learning_profile": {
        "filename": "learning_profile.md",
        "title": "# Learning Profile",
        "summary_heading": "## Current Learning Profile",
        "history_heading": "## Profile Updates",
    },
}


class MemoryService:
    def __init__(
        self,
        settings: Settings | None = None,
        model_service: ModelService | None = None,
        rag_service: RagService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.model_service = model_service or ModelService()
        self.rag_service = rag_service or RagService(self.settings)
        self.session_repository = InterviewSessionRepository()
        self.turn_repository = InterviewTurnRepository()
        self.report_repository = ReportRepository()
        self.memory_repository = MemoryFileRepository()

    def finish_session(self, session: Session, interview_session_id: str) -> tuple[InterviewSession, Report]:
        interview_session = self.session_repository.get(session, interview_session_id)
        if interview_session is None:
            raise not_found("session_not_found", "Interview session not found.")
        if interview_session.status != "active":
            raise conflict("session_not_active", "Interview session is not active.")
        config = session.get(InterviewConfig, interview_session.config_id)
        if config is None:
            raise not_found("config_not_found", "Interview config not found.")

        turns = self.turn_repository.list_for_session(session, interview_session.id)
        report_markdown = self.model_service.generate_report(
            ReportGenerationRequest(
                session_id=interview_session.id,
                turns=[
                    {
                        "round_index": turn.round_index,
                        "question": turn.question,
                        "answer": turn.answer,
                        "feedback": turn.feedback,
                        "missing_points": turn.missing_points,
                        "weaknesses": turn.weaknesses,
                        "review_suggestions": turn.review_suggestions,
                    }
                    for turn in turns
                ],
                provider=config.chat_model_provider,
                model=config.chat_model,
            )
        )
        report_path = self.settings.data_dir / "reports" / f"{interview_session.id}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_markdown, encoding="utf-8")

        weaknesses = sorted({weakness for turn in turns for weakness in turn.weaknesses})
        report = self.report_repository.add(
            session,
            Report(
                session_id=interview_session.id,
                report_path=str(report_path),
                summary=f"{config.target_role} interview for {config.target_company}".strip(),
                weaknesses=weaknesses,
            ),
        )
        memory_files = self._update_memory_files(
            session,
            report_markdown,
            config.chat_model_provider,
            config.chat_model,
        )
        interview_session.status = "completed"
        interview_session.ended_at = datetime.now(UTC)
        interview_session.report_path = str(report_path)
        self.index_report_and_memory(session, report, memory_files)
        session.flush()
        return interview_session, report

    def ensure_memory_files(self) -> dict[str, Path]:
        memory_dir = self.settings.data_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        for kind, layout in MEMORY_LAYOUT.items():
            path = memory_dir / layout["filename"]
            if not path.exists():
                path.write_text(
                    "\n\n".join(
                        [
                            layout["title"],
                            layout["summary_heading"],
                            "No completed interviews yet.",
                            layout["history_heading"],
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
            paths[kind] = path
        return paths

    def read_memory(self) -> dict[str, tuple[str, datetime | None]]:
        paths = self.ensure_memory_files()
        files: dict[str, tuple[str, datetime | None]] = {}
        for kind, path in paths.items():
            updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            files[kind] = (path.read_text(encoding="utf-8"), updated_at)
        return files

    def index_report_and_memory(
        self, session: Session, report: Report, memory_files: list[MemoryFile]
    ) -> None:
        del session
        chunks: list[ChromaChunk] = []
        report_text = Path(report.report_path).read_text(encoding="utf-8")
        for index, chunk in enumerate(self.rag_service.split_text(report_text)):
            chunks.append(
                ChromaChunk(
                    id=f"report:{report.id}:{index}",
                    content=chunk,
                    embedding=self.rag_service.embed_texts([chunk])[0],
                    metadata={
                        "source_type": "report",
                        "session_id": report.session_id,
                        "source_id": report.id,
                        "chunk_index": index,
                        "collection": self.settings.default_collection,
                        "title": "Interview Report",
                    },
                )
            )
        for memory_file in memory_files:
            text = Path(memory_file.file_path).read_text(encoding="utf-8")
            for index, chunk in enumerate(self.rag_service.split_text(text)):
                chunks.append(
                    ChromaChunk(
                        id=f"memory:{memory_file.kind}:{index}",
                        content=chunk,
                        embedding=self.rag_service.embed_texts([chunk])[0],
                        metadata={
                            "source_type": "memory",
                            "memory_kind": memory_file.kind,
                            "source_id": memory_file.id,
                            "chunk_index": index,
                            "collection": self.settings.default_collection,
                            "title": memory_file.kind,
                        },
                    )
                )
        self.rag_service.chroma_store.upsert_chunks(self.settings.default_collection, chunks)

    def _update_memory_files(
        self,
        session: Session,
        report_markdown: str,
        provider: str,
        model: str,
    ) -> list[MemoryFile]:
        paths = self.ensure_memory_files()
        update = self.model_service.update_memory(
            MemoryUpdateRequest(
                report_markdown=report_markdown,
                existing_memory={
                    kind: path.read_text(encoding="utf-8") for kind, path in paths.items()
                },
                provider=provider,
                model=model,
            )
        )
        summaries = {
            "weakness": update.weakness_summary,
            "interview_history": update.interview_summary,
            "learning_profile": update.learning_profile,
        }
        memory_files: list[MemoryFile] = []
        for kind, path in paths.items():
            summary = summaries[kind]
            content = self._rewrite_memory(path.read_text(encoding="utf-8"), kind, summary)
            path.write_text(content, encoding="utf-8")
            memory_file = self.memory_repository.get_by_kind(session, kind)
            if memory_file is None:
                memory_file = self.memory_repository.add(
                    session,
                    MemoryFile(kind=kind, file_path=str(path), summary_hash=""),
                )
            memory_file.file_path = str(path)
            memory_file.summary_hash = str(abs(hash(summary)))
            memory_file.updated_at = datetime.now(UTC)
            memory_files.append(memory_file)
        session.flush()
        return memory_files

    def _rewrite_memory(self, content: str, kind: str, summary: str) -> str:
        layout = MEMORY_LAYOUT[kind]
        entry = f"### {datetime.now(UTC).isoformat()}\n{summary}"
        return "\n\n".join(
            [
                layout["title"],
                layout["summary_heading"],
                summary,
                layout["history_heading"],
                self._existing_history(content, layout["history_heading"], entry),
            ]
        ).strip() + "\n"

    def _existing_history(self, content: str, history_heading: str, entry: str) -> str:
        if history_heading not in content:
            return entry
        existing = content.split(history_heading, 1)[1].strip()
        return f"{existing}\n\n{entry}".strip()
