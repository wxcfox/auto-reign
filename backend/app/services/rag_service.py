import hashlib
import math
import re
from typing import Any

from fastapi import HTTPException
from openai import OpenAI
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import bad_gateway, not_found, service_unavailable
from app.db.models import Document, DocumentChunk
from app.repositories.chroma_store import ChromaChunk, ChromaStore
from app.repositories.database import DocumentChunkRepository, DocumentRepository


class RagService:
    def __init__(
        self,
        settings: Settings | None = None,
        chroma_store: ChromaStore | None = None,
        document_repository: DocumentRepository | None = None,
        chunk_repository: DocumentChunkRepository | None = None,
        embedding_client: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.chroma_store = chroma_store or ChromaStore(self.settings.data_dir / "chroma")
        self.document_repository = document_repository or DocumentRepository()
        self.chunk_repository = chunk_repository or DocumentChunkRepository()
        self.embedding_client = embedding_client

    def split_text(self, text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
        compact = text.strip()
        if not compact:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(compact):
            end = min(start + chunk_size, len(compact))
            chunks.append(compact[start:end])
            if end == len(compact):
                break
            start = max(end - overlap, start + 1)
        return chunks

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.settings.deterministic_model_fallback:
            return [self._embed_text(text) for text in texts]
        if self.settings.embedding_provider != "openai" or not self.settings.openai_api_key:
            raise service_unavailable(
                "embedding_provider_not_configured",
                "The configured embedding provider is not available.",
            )
        try:
            client = self.embedding_client or OpenAI(api_key=self.settings.openai_api_key)
            response = client.embeddings.create(
                input=texts,
                model=self.settings.embedding_model,
                encoding_format="float",
            )
            return [list(item.embedding) for item in response.data]
        except HTTPException:
            raise
        except Exception as error:
            raise bad_gateway(
                "embedding_call_failed",
                "The embedding provider request failed.",
            ) from error

    def index_document(self, session: Session, document: Document) -> Document:
        text = self._read_document_text(document)
        chunks = self.split_text(text)
        embeddings = self.embed_texts(chunks)
        self.chroma_store.delete_document_chunks(self.settings.qdrant_collection, document.id)
        self.chunk_repository.delete_for_document(session, document.id)

        chroma_chunks: list[ChromaChunk] = []
        db_chunks: list[DocumentChunk] = []
        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            vector_id = f"document:{document.id}:{index}"
            chroma_chunks.append(
                ChromaChunk(
                    id=vector_id,
                    content=chunk,
                    embedding=embedding,
                    metadata={
                        "source_type": "document",
                        "document_id": document.id,
                        "source_id": document.id,
                        "chunk_index": index,
                        "collection": document.collection,
                        "title": document.title,
                        "tags": ",".join(document.tags),
                    },
                )
            )
            db_chunks.append(
                DocumentChunk(
                    document_id=document.id,
                    chunk_index=index,
                    content_hash=hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                    vector_collection=self.settings.qdrant_collection,
                    vector_id=vector_id,
                )
            )

        self.chroma_store.upsert_chunks(self.settings.qdrant_collection, chroma_chunks)
        self.chunk_repository.add_many(session, db_chunks)
        document.index_status = "completed"
        session.flush()
        return document

    def reindex_document(self, session: Session, document_id: str) -> Document:
        document = self.document_repository.get(session, document_id)
        if document is None:
            raise not_found("document_not_found", "Document not found.")
        return self.index_document(session, document)

    def search(self, session: Session, query: str, limit: int) -> list[dict[str, object]]:
        del session
        query_embedding = self.embed_texts([query])[0]
        raw_hits = self.chroma_store.search(self.settings.qdrant_collection, query_embedding, limit)
        hits: list[dict[str, object]] = []
        for hit in raw_hits:
            metadata = hit["metadata"]
            hits.append(
                {
                    "content": hit["content"],
                    "score": hit["score"],
                    "source_type": str(metadata.get("source_type", "")),
                    "source_id": str(metadata.get("source_id") or metadata.get("document_id") or ""),
                }
            )
        return hits

    def _embed_text(self, text: str) -> list[float]:
        vector = [0.0] * 32
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", text.lower())
        for word in words or [text.lower()]:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            index = digest[0] % len(vector)
            sign = 1.0 if digest[1] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def _read_document_text(self, document: Document) -> str:
        return document.file_path and open(document.file_path, encoding="utf-8").read()
