from datetime import UTC, datetime
from types import SimpleNamespace

from langchain_core.documents import Document

from app.services.artifact_document_service import (
    ArtifactDocumentBuilder,
    ArtifactTextSplitter,
)


def artifact(**overrides):
    metadata_json = {
        "source_refs": ["source:abc"],
        "evidence_refs": ["practice:def"],
        "language": "zh-CN",
        "origin": "human",
        "edited_by": "user",
    }
    values = {
        "id": "artifact-1",
        "kind": "knowledge",
        "relative_path": "knowledge/redis.md",
        "revision": 3,
        "metadata_json": metadata_json,
        "created_at": datetime(2026, 6, 25, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 25, tzinfo=UTC),
    }
    if "source_refs" in overrides:
        metadata_json["source_refs"] = overrides.pop("source_refs")
    if "evidence_refs" in overrides:
        metadata_json["evidence_refs"] = overrides.pop("evidence_refs")
    if "language" in overrides:
        metadata_json["language"] = overrides.pop("language")
    values.update(overrides)
    return SimpleNamespace(**values)


def test_builder_preserves_artifact_metadata() -> None:
    source_refs = ["source:abc"]
    document = ArtifactDocumentBuilder().build(
        artifact(source_refs=source_refs),
        "# Redis\n\n缓存击穿",
    )
    source_refs.append("source:mutated")

    assert isinstance(document, Document)
    assert document.page_content == "# Redis\n\n缓存击穿"
    assert document.metadata["artifact_id"] == "artifact-1"
    assert document.metadata["source_id"] == "artifact-1"
    assert document.metadata["document_id"] == "artifact-1"
    assert document.metadata["artifact_kind"] == "knowledge"
    assert document.metadata["source_type"] == "artifact"
    assert document.metadata["relative_path"] == "knowledge/redis.md"
    assert document.metadata["source_refs"] == ["source:abc"]
    assert document.metadata["evidence_refs"] == ["practice:def"]
    assert document.metadata["language"] == "zh-CN"
    assert document.metadata["revision"] == 3


def test_markdown_splitter_preserves_header_metadata() -> None:
    document = ArtifactDocumentBuilder().build(
        artifact(),
        (
            "# Redis\n\n"
            "## 缓存击穿\n\n"
            "互斥锁和逻辑过期。\n\n"
            "## 缓存穿透\n\n"
            "布隆过滤器。\n\n"
            "### 防护细节\n\n"
            "缓存空值和限流。\n"
        ),
    )

    chunks = ArtifactTextSplitter(chunk_size=80, chunk_overlap=10).split([document])

    assert len(chunks) >= 2
    assert all(chunk.metadata["artifact_id"] == "artifact-1" for chunk in chunks)
    assert all(chunk.metadata["source_refs"] == ["source:abc"] for chunk in chunks)
    assert all(chunk.metadata["revision"] == 3 for chunk in chunks)
    assert {chunk.metadata.get("h1") for chunk in chunks} == {"Redis"}
    assert {chunk.metadata.get("h2") for chunk in chunks} >= {"缓存击穿", "缓存穿透"}
    assert "防护细节" in {chunk.metadata.get("h3") for chunk in chunks}
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == list(range(len(chunks)))


def test_splitter_resets_chunk_index_per_artifact() -> None:
    first = ArtifactDocumentBuilder().build(
        artifact(id="artifact-1", relative_path="knowledge/redis.md"),
        "# Redis\n\n## 缓存击穿\n\n" + "互斥锁和逻辑过期。" * 12,
    )
    second = ArtifactDocumentBuilder().build(
        artifact(id="artifact-2", relative_path="knowledge/mysql.md"),
        "# MySQL\n\n## 索引\n\n" + "联合索引和回表。" * 12,
    )

    chunks = ArtifactTextSplitter(chunk_size=40, chunk_overlap=5).split([first, second])
    indices_by_artifact = {
        artifact_id: [
            chunk.metadata["chunk_index"]
            for chunk in chunks
            if chunk.metadata["artifact_id"] == artifact_id
        ]
        for artifact_id in {"artifact-1", "artifact-2"}
    }

    assert set(indices_by_artifact) == {"artifact-1", "artifact-2"}
    assert all(indices for indices in indices_by_artifact.values())
    assert indices_by_artifact["artifact-1"] == list(
        range(len(indices_by_artifact["artifact-1"]))
    )
    assert indices_by_artifact["artifact-2"] == list(
        range(len(indices_by_artifact["artifact-2"]))
    )
