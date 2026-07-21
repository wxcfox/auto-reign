from langchain_core.embeddings import Embeddings

from app.core.config import Settings
from app.repositories.vector_store import VectorStoreUnavailable
from app.services.embedding_service import EmbeddingService


def build_knowledge_embeddings(settings: Settings) -> Embeddings:
    try:
        return EmbeddingService(settings).embeddings
    except Exception as error:
        raise VectorStoreUnavailable(
            "Knowledge embedding construction failed"
        ) from error
