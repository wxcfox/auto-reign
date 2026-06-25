from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter


class ArtifactDocumentBuilder:
    def build(self, artifact: Any, body: str) -> Document:
        return Document(
            page_content=body,
            metadata={
                "artifact_id": artifact.id,
                "source_id": artifact.id,
                "document_id": artifact.id,
                "artifact_kind": artifact.kind,
                "source_type": "artifact",
                "relative_path": artifact.relative_path,
                "revision": artifact.revision,
                "source_refs": list(artifact.source_refs or []),
                "evidence_refs": list(artifact.evidence_refs or []),
                "language": artifact.language,
            },
        )


class ArtifactTextSplitter:
    def __init__(self, *, chunk_size: int = 900, chunk_overlap: int = 120) -> None:
        self.markdown_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "h1"),
                ("##", "h2"),
                ("###", "h3"),
                ("####", "h4"),
            ],
            strip_headers=False,
        )
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", ".", " ", ""],
        )

    def split(self, documents: list[Document]) -> list[Document]:
        chunks: list[Document] = []
        for document in documents:
            section_docs = self._split_markdown(document)
            document_chunks: list[Document] = []
            for section_doc in section_docs:
                document_chunks.extend(self.recursive_splitter.split_documents([section_doc]))
            for index, chunk in enumerate(document_chunks):
                chunk.metadata = {**chunk.metadata, "chunk_index": index}
            chunks.extend(document_chunks)
        return chunks

    def _split_markdown(self, document: Document) -> list[Document]:
        sections = self.markdown_splitter.split_text(document.page_content)
        if not sections:
            return [document]
        return [
            Document(
                page_content=section.page_content,
                metadata={**document.metadata, **section.metadata},
            )
            for section in sections
        ]
