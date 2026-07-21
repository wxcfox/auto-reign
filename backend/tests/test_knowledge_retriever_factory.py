from dataclasses import dataclass

import pytest

from app.services.knowledge_retrievers.factory import KnowledgeRetrieverFactory


@dataclass
class StubRetriever:
    retriever_type: str
    supported_retrieval_methods: frozenset[str]
    healthy: bool = True

    def test_connection(self) -> bool:
        return self.healthy


def test_factory_selects_shared_retrievers_and_reports_capabilities() -> None:
    elasticsearch = StubRetriever(
        retriever_type="elasticsearch",
        supported_retrieval_methods=frozenset({"vector", "keyword", "hybrid"}),
    )
    qdrant = StubRetriever(
        retriever_type="qdrant",
        supported_retrieval_methods=frozenset({"vector"}),
        healthy=False,
    )
    factory = KnowledgeRetrieverFactory(
        retrievers={
            "elasticsearch": elasticsearch,  # type: ignore[dict-item]
            "qdrant": qdrant,  # type: ignore[dict-item]
        }
    )

    assert factory.get("elasticsearch") is elasticsearch
    assert factory.get("qdrant") is qdrant
    assert factory.supported_retrieval_methods("elasticsearch") == (
        "hybrid",
        "keyword",
        "vector",
    )
    assert factory.supported_retrieval_methods("qdrant") == ("vector",)
    assert factory.test_connections() == {
        "elasticsearch": True,
        "qdrant": False,
    }


def test_factory_rejects_missing_or_mismatched_shared_backends() -> None:
    elasticsearch = StubRetriever(
        retriever_type="elasticsearch",
        supported_retrieval_methods=frozenset({"vector"}),
    )
    with pytest.raises(ValueError, match="both required"):
        KnowledgeRetrieverFactory(
            retrievers={"elasticsearch": elasticsearch}  # type: ignore[dict-item]
        )

    with pytest.raises(ValueError, match="does not match"):
        KnowledgeRetrieverFactory(
            retrievers={
                "elasticsearch": elasticsearch,  # type: ignore[dict-item]
                "qdrant": elasticsearch,  # type: ignore[dict-item]
            }
        )
