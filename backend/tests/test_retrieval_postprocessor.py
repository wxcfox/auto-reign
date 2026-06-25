from app.services.retrieval_postprocessor import RetrievalPostProcessor
from app.services.retrieval_query_planner import RetrievalQueryPlan
from app.services.workspace_vector_store import WorkspaceVectorHit


def plan(
    *,
    final_limit: int = 3,
    score_threshold: float = 0.5,
    max_per_artifact: int = 1,
) -> RetrievalQueryPlan:
    return RetrievalQueryPlan(
        semantic_query="redis",
        artifact_kinds=("knowledge", "question_bank", "project"),
        candidate_limit=10,
        final_limit=final_limit,
        score_threshold=score_threshold,
        max_per_artifact=max_per_artifact,
        purpose="question_generation",
    )


def hit(content: str, score: float, artifact_id: str, kind: str) -> WorkspaceVectorHit:
    return WorkspaceVectorHit(
        content=content,
        score=score,
        metadata={"artifact_id": artifact_id, "artifact_kind": kind, "source_type": "artifact"},
    )


def test_postprocessor_filters_low_scores_and_caps_per_artifact() -> None:
    hits = [
        hit("a1 first", 0.9, "a1", "knowledge"),
        hit("a1 second", 0.8, "a1", "knowledge"),
        hit("a2", 0.7, "a2", "project"),
        hit("low", 0.2, "a3", "question_bank"),
    ]

    processed = RetrievalPostProcessor().process(hits, plan())

    assert [item.content for item in processed] == ["a1 first", "a2"]


def test_postprocessor_keeps_multiple_kinds_when_available() -> None:
    hits = [
        hit("k1", 0.9, "a1", "knowledge"),
        hit("k2", 0.89, "a2", "knowledge"),
        hit("q1", 0.88, "a3", "question_bank"),
        hit("p1", 0.87, "a4", "project"),
    ]

    processed = RetrievalPostProcessor().process(hits, plan())

    assert [item.content for item in processed] == ["k1", "q1", "p1"]
    assert {item.metadata["artifact_kind"] for item in processed} == {
        "knowledge",
        "question_bank",
        "project",
    }


def test_postprocessor_sorts_by_score_before_final_limit() -> None:
    hits = [
        hit("middle", 0.7, "a2", "project"),
        hit("top", 0.9, "a1", "knowledge"),
        hit("bottom", 0.6, "a3", "question_bank"),
    ]

    processed = RetrievalPostProcessor().process(hits, plan(final_limit=2, max_per_artifact=2))

    assert [item.content for item in processed] == ["top", "middle"]


def test_postprocessor_uses_source_id_when_artifact_id_is_missing() -> None:
    hits = [
        WorkspaceVectorHit(
            content="first",
            score=0.9,
            metadata={"source_id": "source-1", "artifact_kind": "knowledge"},
        ),
        WorkspaceVectorHit(
            content="second",
            score=0.8,
            metadata={"source_id": "source-1", "artifact_kind": "knowledge"},
        ),
        WorkspaceVectorHit(
            content="third",
            score=0.7,
            metadata={"source_id": "source-2", "artifact_kind": "project"},
        ),
    ]

    processed = RetrievalPostProcessor().process(hits, plan())

    assert [item.content for item in processed] == ["first", "third"]
