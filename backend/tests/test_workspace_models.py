from datetime import UTC, datetime

from sqlalchemy import inspect, select


def test_workspace_models_store_projection_and_job_state(client) -> None:
    from app.db.models import Artifact, ProcessingJob, WorkspaceSettings
    from app.db.session import session_scope

    with session_scope(client.app.state.session_factory) as session:
        settings = session.get(WorkspaceSettings, "default")
        assert settings is not None
        settings.embedding_config = "qwen:text-embedding-v4"
        artifact = Artifact(
            id="artifact-1",
            kind="knowledge",
            relative_path="knowledge/redis.md",
            content_hash="hash-1",
            revision=3,
            source_refs=["source:resume"],
            evidence_refs=[],
            processing_status="needs_recovery",
            index_status="stale",
            language="zh-CN",
            origin="human",
            edited_by="user",
        )
        session.add(artifact)
        session.flush()
        job = ProcessingJob(
            operation="reindex",
            artifact_id=artifact.id,
            idempotency_key="reindex:artifact-1:3",
        )
        session.add(job)
        session.flush()

        loaded_settings = session.get(WorkspaceSettings, "default")
        loaded_artifact = session.get(Artifact, "artifact-1")
        assert loaded_settings is not None
        assert loaded_settings.language == "zh-CN"
        assert loaded_artifact is not None
        assert loaded_artifact.source_refs == ["source:resume"]
        assert loaded_artifact.processing_status == "needs_recovery"
        assert loaded_artifact.index_status == "stale"
        assert job.status == "pending"
        assert job.attempts == 0


def test_workspace_settings_uses_one_fixed_identity(client) -> None:
    from app.db.models import WorkspaceSettings
    from app.db.session import session_scope

    with session_scope(client.app.state.session_factory) as session:
        loaded = session.get(WorkspaceSettings, "default")
        rows = list(session.scalars(select(WorkspaceSettings)))
        assert loaded is not None
        assert rows == [loaded]


def test_workspace_front_matter_persists_recovery_semantics() -> None:
    from app.schemas.workspace import ArtifactFrontMatter

    metadata = ArtifactFrontMatter(
        id="recovered-1",
        kind="practice",
        language="zh-CN",
        revision=1,
        created_at=datetime(2026, 6, 22, tzinfo=UTC),
        updated_at=datetime(2026, 6, 22, tzinfo=UTC),
        origin="human",
        edited_by="user",
        recovery_required=True,
        recovery_reason="missing_front_matter",
    )

    assert metadata.recovery_required is True
    assert metadata.recovery_reason == "missing_front_matter"


def test_workspace_tables_exist_in_test_schema(client) -> None:
    from app.db.session import session_scope

    with session_scope(client.app.state.session_factory) as session:
        tables = set(inspect(session.get_bind()).get_table_names())

    assert {"workspace_settings", "artifacts", "processing_jobs"} <= tables
