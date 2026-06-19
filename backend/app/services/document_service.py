import re
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import bad_request
from app.db.models import Document
from app.repositories.sqlite import DocumentRepository
from app.services.model_service import ModelService


class DocumentService:
    def __init__(
        self,
        model_service: ModelService | None = None,
        document_repository: DocumentRepository | None = None,
    ) -> None:
        self.model_service = model_service or ModelService()
        self.document_repository = document_repository or DocumentRepository()

    async def upload_document(self, session: Session, upload_file: UploadFile) -> Document:
        source_filename = upload_file.filename or "document.txt"
        file_type = self._file_type(source_filename)
        raw_content = await upload_file.read()
        text = raw_content.decode("utf-8")
        analysis = self.model_service.analyze_document(text)

        settings = get_settings()
        document_id = str(uuid4())
        safe_filename = self._safe_filename(source_filename)
        file_path = settings.data_dir / "uploads" / f"{document_id}-{safe_filename}"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(raw_content)

        document = Document(
            id=document_id,
            collection=settings.default_collection,
            source_filename=source_filename,
            file_path=str(file_path),
            file_type=file_type,
            title=analysis.title,
            summary=analysis.summary,
            tags=analysis.tags,
            knowledge_points=analysis.knowledge_points,
            weakness_candidates=analysis.weakness_candidates,
            analysis_status="completed",
            index_status="pending",
        )
        return self.document_repository.add(session, document)

    def _file_type(self, filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix == ".md":
            return "markdown"
        if suffix == ".txt":
            return "txt"
        raise bad_request("unsupported_file_type", "Only Markdown and TXT uploads are supported.")

    def _safe_filename(self, filename: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).name).strip("-")
        return cleaned or "document.txt"
