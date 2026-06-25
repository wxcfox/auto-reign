from __future__ import annotations

import hashlib
import math
import re

from fastapi import HTTPException
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from app.core.config import Settings, get_settings
from app.core.errors import service_unavailable


class DeterministicEmbeddings(Embeddings):
    def __init__(self, dimension: int = 32) -> None:
        self.dimension = dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", text.lower())
        for word in words or [text.lower()]:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            index = digest[0] % len(vector)
            sign = 1.0 if digest[1] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class EmbeddingService:
    def __init__(
        self,
        settings: Settings | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._embeddings = embeddings

    @property
    def embeddings(self) -> Embeddings:
        if self._embeddings is None:
            self._embeddings = self._build_embeddings()
        return self._embeddings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self.embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embeddings.embed_query(text)

    def _build_embeddings(self) -> Embeddings:
        if self.settings.deterministic_model_fallback:
            return DeterministicEmbeddings()
        provider_config = self._resolve_provider()
        if provider_config is None:
            raise service_unavailable(
                "embedding_provider_not_configured",
                "The configured embedding provider is not available.",
            )
        try:
            api_key, base_url = provider_config
            kwargs: dict[str, object] = {
                "model": self.settings.embedding_model,
                "api_key": api_key,
            }
            if base_url:
                kwargs["base_url"] = base_url
                kwargs["check_embedding_ctx_length"] = False
                kwargs["model_kwargs"] = {"encoding_format": "float"}
            return OpenAIEmbeddings(**kwargs)
        except HTTPException:
            raise
        except Exception as exc:
            raise service_unavailable(
                "embedding_provider_not_configured",
                "The configured embedding provider is not available.",
            ) from exc

    def _resolve_provider(self) -> tuple[str, str | None] | None:
        if self.settings.embedding_provider == "openai":
            if not self.settings.openai_api_key:
                return None
            return self.settings.openai_api_key, None
        if self.settings.embedding_provider == "qwen":
            if not self.settings.qwen_api_key:
                return None
            return self.settings.qwen_api_key, self.settings.qwen_base_url
        return None
