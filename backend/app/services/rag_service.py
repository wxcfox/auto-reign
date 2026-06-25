import hashlib
import logging
import math
import re
from typing import Any

from fastapi import HTTPException
from openai import OpenAI
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import bad_gateway, service_unavailable
from app.repositories.qdrant_store import get_qdrant_store
from app.repositories.vector_store import (
    VectorDimensionMismatch,
    VectorStore,
    VectorStoreUnavailable,
)

logger = logging.getLogger(__name__)


class RagService:
    def __init__(
        self,
        settings: Settings | None = None,
        vector_store: VectorStore | None = None,
        embedding_client: Any | None = None,
        embedding_client_factory: Any = OpenAI,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_store = vector_store or get_qdrant_store()
        self.embedding_client = embedding_client
        self.embedding_client_factory = embedding_client_factory

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
        provider_config = self._resolve_embedding_provider()
        if provider_config is None:
            raise service_unavailable(
                "embedding_provider_not_configured",
                "The configured embedding provider is not available.",
            )
        try:
            provider_name, api_key, base_url = provider_config
            client = self.embedding_client or self.embedding_client_factory(
                api_key=api_key,
                base_url=base_url,
            )
            response = client.embeddings.create(
                input=texts,
                model=self.settings.embedding_model,
                encoding_format="float",
            )
            return [list(item.embedding) for item in response.data]
        except HTTPException:
            raise
        except Exception as error:
            logger.exception(
                "Embedding provider request failed: provider=%s model=%s error_type=%s error_message=%s",
                self.settings.embedding_provider,
                self.settings.embedding_model,
                type(error).__name__,
                str(error),
                extra={
                    "provider": self.settings.embedding_provider,
                    "model": self.settings.embedding_model,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                },
            )
            raise bad_gateway(
                "embedding_call_failed",
                "The embedding provider request failed.",
            ) from error

    def _resolve_embedding_provider(self) -> tuple[str, str, str | None] | None:
        providers = {
            "openai": (self.settings.openai_api_key, None),
            "qwen": (self.settings.qwen_api_key, self.settings.qwen_base_url),
        }
        provider_name = self.settings.embedding_provider
        if provider_name not in providers:
            return None
        api_key, base_url = providers[provider_name]
        if not api_key:
            return None
        return provider_name, api_key, base_url

    def search(self, session: Session, query: str, limit: int) -> list[dict[str, object]]:
        del session
        if not self.vector_store.has_searchable_content(self.settings.qdrant_collection):
            return []
        query_embedding = self.embed_texts([query])[0]
        try:
            raw_hits = self.vector_store.search(
                self.settings.qdrant_collection, query_embedding, limit
            )
        except VectorDimensionMismatch as error:
            raise service_unavailable(
                "vector_dimension_mismatch",
                "The configured vector collection is incompatible with the embedding dimension.",
            ) from error
        except VectorStoreUnavailable as error:
            raise service_unavailable(
                "vector_store_unavailable",
                "The vector store is currently unavailable.",
            ) from error
        hits: list[dict[str, object]] = []
        for hit in raw_hits:
            metadata = hit.metadata
            hits.append(
                {
                    "content": hit.content,
                    "score": hit.score,
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
