from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.knowledge_collections import KnowledgeCollectionConfig
from app.services.knowledge_chunk_service import KnowledgeChunkService


def assert_exact_source_ranges(
    text: str,
    chunks,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    assert chunks
    assert chunks[0].metadata["source_start"] == 0
    assert chunks[-1].metadata["source_end"] == len(text)

    reconstructed = chunks[0].content
    covered_end = chunks[0].metadata["source_end"]
    for index, chunk in enumerate(chunks):
        start = chunk.metadata["source_start"]
        end = chunk.metadata["source_end"]
        assert type(start) is int
        assert type(end) is int
        assert start < end
        assert len(chunk.content) <= chunk_size
        assert chunk.content == text[start:end]
        assert chunk.content in text
        if index == 0:
            continue
        previous_end = chunks[index - 1].metadata["source_end"]
        assert start == previous_end - chunk_overlap
        assert start <= covered_end <= end
        reconstructed += chunk.content[covered_end - start :]
        covered_end = end

    assert reconstructed == text


def test_chunks_are_source_derived_without_generated_summary() -> None:
    text = (
        "# Cache\nRedis cache penetration.\n\n"
        "## Mitigation\nUse request coalescing."
    )
    chunks = KnowledgeChunkService(chunk_size=48, chunk_overlap=8).split(
        document_id="doc-1",
        collection_id="collection-1",
        owner_user_id=7,
        generation=3,
        content_hash="sha256-source",
        filename="cache.md",
        text=text,
    )

    assert_exact_source_ranges(
        text,
        chunks,
        chunk_size=48,
        chunk_overlap=8,
    )
    assert {chunk.metadata["index_generation"] for chunk in chunks} == {3}
    assert {chunk.metadata["collection_id"] for chunk in chunks} == {
        "collection-1"
    }
    assert {chunk.metadata["owner_user_id"] for chunk in chunks} == {7}
    assert {chunk.metadata["document_id"] for chunk in chunks} == {"doc-1"}
    assert {chunk.metadata["content_hash"] for chunk in chunks} == {
        "sha256-source"
    }
    assert {chunk.metadata["filename"] for chunk in chunks} == {"cache.md"}
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == list(
        range(len(chunks))
    )


def test_chunk_service_preserves_source_metadata_and_offsets() -> None:
    text = "# First\nAlpha text.\n\n## Second\nBeta text."
    chunks = KnowledgeChunkService(chunk_size=40, chunk_overlap=5).split(
        marker={"origin": "test"},
        text=text,
    )

    assert_exact_source_ranges(
        text,
        chunks,
        chunk_size=40,
        chunk_overlap=5,
    )
    assert all(chunk.metadata["marker"] == {"origin": "test"} for chunk in chunks)


@pytest.mark.parametrize(
    "text",
    [
        (
            "def handler():\n"
            "    settings = {\n"
            '        "enabled": True,\n'
            "    }\n"
            "    return settings\n"
            "\n"
            "service:\n"
            "  nested:\n"
            "    enabled: true\n"
        ),
        (
            "# Example\n\n"
            "```python\n"
            "def greet(name):\n"
            '    return f"hello {name}"\n'
            "```\n\n"
            "```yaml\n"
            "service:\n"
            "  ports:\n"
            "    - 8080\n"
            "```\n"
        ),
        "alpha\n\n\n\n  indented\n \n\tcontinued\n\n\ntrailing  spaces   \n",
    ],
    ids=["indented-python-yaml", "fenced-code", "continuous-whitespace"],
)
def test_chunk_content_is_a_verbatim_source_slice(text: str) -> None:
    chunks = KnowledgeChunkService(chunk_size=37, chunk_overlap=7).split(text=text)

    assert_exact_source_ranges(
        text,
        chunks,
        chunk_size=37,
        chunk_overlap=7,
    )


def test_zero_overlap_chunks_concatenate_to_exact_source() -> None:
    text = "first line\n\n    indented block\n\nlast line\n"
    chunks = KnowledgeChunkService(chunk_size=19, chunk_overlap=0).split(text=text)

    assert_exact_source_ranges(
        text,
        chunks,
        chunk_size=19,
        chunk_overlap=0,
    )
    assert "".join(chunk.content for chunk in chunks) == text


def test_chunk_service_returns_no_chunks_for_blank_source() -> None:
    assert KnowledgeChunkService().split(text=" \n\t ") == []


def test_chunk_service_rejects_non_text_source() -> None:
    with pytest.raises(ValueError, match="must be a string"):
        KnowledgeChunkService().split(text=b"bytes")


def test_collection_config_defines_bounded_generation_settings() -> None:
    config = KnowledgeCollectionConfig.model_validate({})

    assert config.model_dump() == {
        "retriever_type": "elasticsearch",
        "retrieval_mode": "vector",
        "chunk_size": 900,
        "chunk_overlap": 120,
        "top_k": 5,
        "score_threshold": 0.5,
        "vector_weight": 0.7,
        "keyword_weight": 0.3,
    }
    assert KnowledgeChunkService.from_config(config)


@pytest.mark.parametrize(
    "payload",
    [
        {"chunk_size": 199},
        {"chunk_size": 4_001},
        {"chunk_overlap": -1},
        {"chunk_overlap": 1_001},
        {"chunk_size": 200, "chunk_overlap": 200},
        {"chunk_size": 1_001, "chunk_overlap": 1_000},
        {"chunk_size": 200, "chunk_overlap": 101},
        {"top_k": 0},
        {"top_k": 31},
        {"score_threshold": -1.01},
        {"score_threshold": 1.01},
        {"unknown": True},
    ],
)
def test_collection_config_rejects_invalid_or_unknown_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        KnowledgeCollectionConfig.model_validate(payload)


def test_collection_config_accepts_overlap_at_half_boundary() -> None:
    config = KnowledgeCollectionConfig.model_validate(
        {"chunk_size": 200, "chunk_overlap": 100}
    )

    assert config.chunk_overlap == 100


@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap"),
    [(1_001, 1_000), (200, 101)],
)
def test_chunk_service_rejects_overlap_above_half(
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    with pytest.raises(ValueError, match="must not exceed half"):
        KnowledgeChunkService(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )


def test_chunk_service_accepts_overlap_at_half_boundary() -> None:
    service = KnowledgeChunkService(chunk_size=200, chunk_overlap=100)

    assert service.chunk_overlap == 100
