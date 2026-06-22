from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


@dataclass(frozen=True)
class ExtractedText:
    text: str
    should_write_extracted_artifact: bool


class ExtractionService:
    def extract(self, filename: str, media_type: str, content: bytes) -> ExtractedText | None:
        suffix = Path(filename).suffix.lower()
        if media_type in {"text/markdown", "text/plain"} or suffix in {".md", ".txt"}:
            return ExtractedText(content.decode("utf-8"), should_write_extracted_artifact=False)
        if suffix == ".docx" or media_type.endswith("wordprocessingml.document"):
            return ExtractedText(self._extract_docx(content), should_write_extracted_artifact=True)
        if suffix == ".pdf" or media_type == "application/pdf":
            text = self._extract_pdf(content)
            return ExtractedText(text, should_write_extracted_artifact=True) if text else None
        return None

    def _extract_docx(self, content: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
        return "\n".join(text for text in texts if text).strip()

    def _extract_pdf(self, content: bytes) -> str:
        try:
            from pypdf import PdfReader
        except Exception:
            return ""
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
