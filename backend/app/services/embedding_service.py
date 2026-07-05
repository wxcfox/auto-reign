from __future__ import annotations

from fastapi import HTTPException
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from app.core.config import Settings, get_settings
from app.core.errors import service_unavailable


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
