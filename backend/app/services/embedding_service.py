from __future__ import annotations

import time

from fastapi import HTTPException
from langchain_core.embeddings import Embeddings
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.core.config import Settings, get_settings
from app.core.errors import service_unavailable
from app.repositories.vector_store import VectorStoreUnavailable

EMBEDDING_MAX_RETRIES = 3
EMBEDDING_TIMEOUT_SECONDS = 30.0


class EmbeddingProviderError(VectorStoreUnavailable):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SingleRequestEmbeddings(Embeddings):
    """OpenAI-compatible embeddings client that sends one text per request."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None,
    ) -> None:
        self.model = model
        client_kwargs: dict[str, object] = {
            "api_key": api_key,
            "max_retries": 0,
            "timeout": EMBEDDING_TIMEOUT_SECONDS,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    def _embed_one(self, text: str) -> list[float]:
        for attempt in range(EMBEDDING_MAX_RETRIES):
            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=text,
                    encoding_format="float",
                )
                return list(response.data[0].embedding)
            except Exception as error:
                if attempt >= EMBEDDING_MAX_RETRIES - 1 or not _is_retryable(error):
                    raise _embedding_error(error) from error
                time.sleep(min(2**attempt, 10))
        raise AssertionError("embedding retry loop did not return or raise")


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(error, APIStatusError):
        status_code = error.status_code
        return status_code == 429 or status_code >= 500
    return False


def _embedding_error(error: Exception) -> EmbeddingProviderError:
    if isinstance(error, APIStatusError):
        if error.status_code in {401, 403}:
            return EmbeddingProviderError(
                "embedding_auth_failed",
                "Embedding provider authentication failed.",
            )
        if error.status_code == 429:
            return EmbeddingProviderError(
                "embedding_rate_limited",
                "Embedding provider rate limit was exceeded.",
            )
        if 400 <= error.status_code < 500:
            return EmbeddingProviderError(
                "embedding_invalid_request",
                "Embedding provider rejected the request.",
            )
    return EmbeddingProviderError(
        "embedding_provider_unavailable",
        "Embedding provider is unavailable.",
    )


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
            return SingleRequestEmbeddings(
                model=self.settings.embedding_model,
                api_key=api_key,
                base_url=base_url,
            )
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
