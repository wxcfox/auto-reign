from pathlib import Path

from sqlalchemy.orm import Session

from app.db import models
from app.repositories.artifact_repository import ArtifactRepository
from app.services.artifact_service import ArtifactService
from app.services.workspace_service import WorkspaceService


def _session(client) -> Session:
    return client.app.state.session_factory()


def _services(tmp_path: Path) -> tuple[WorkspaceService, ArtifactService, ArtifactRepository]:
    workspace = WorkspaceService(tmp_path / "workspace")
    workspace.initialize()
    return workspace, ArtifactService(workspace), ArtifactRepository()


def test_rebuild_projection_upserts_sources_and_managed_markdown(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _services(tmp_path)
    source = artifacts.store_source(
        source_filename="resume.md",
        media_type="text/markdown",
        content=b"# Resume\n",
    )
    knowledge = artifacts.create_markdown(
        "knowledge/python.md",
        kind="knowledge",
        body="# Python\n",
        source_refs=[f"source:{source.artifact_id}"],
        origin="llm",
    )

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        rows = {artifact.relative_path: artifact for artifact in repository.list(session)}

    assert rows[source.relative_path].id == source.artifact_id
    assert rows[source.relative_path].kind == "source"
    assert rows[source.relative_path].source_filename == "resume.md"
    assert rows[source.relative_path].media_type == "text/markdown"
    assert rows[source.relative_path].origin == "human"
    assert rows["knowledge/python.md"].id == knowledge.front_matter.id
    assert rows["knowledge/python.md"].source_refs == [f"source:{source.artifact_id}"]
    assert rows["knowledge/python.md"].processing_status == "completed"


def test_rebuild_projection_removes_ghost_artifact_and_jobs(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _services(tmp_path)
    with _session(client) as session:
        artifact = models.Artifact(
            id="ghost",
            kind="knowledge",
            relative_path="knowledge/missing.md",
            content_hash="old",
        )
        session.add(artifact)
        session.flush()
        session.add(
            models.ProcessingJob(operation="reindex", artifact_id=artifact.id, status="pending")
        )
        session.commit()

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        assert repository.get(session, "ghost") is None
        assert session.query(models.ProcessingJob).count() == 0


def test_rebuild_projection_repairs_invalid_front_matter_from_existing_projection(
    client, tmp_path: Path
) -> None:
    workspace, artifacts, repository = _services(tmp_path)
    created = artifacts.create_markdown(
        "knowledge/broken.md",
        kind="knowledge",
        body="# Broken\n\nBody stays.\n",
    )
    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        session.commit()

    broken_path = workspace.resolve_path("knowledge/broken.md")
    broken_path.write_text("---\nkind: [not-valid\n---\n# Broken\n\nBody stays.\n", encoding="utf-8")

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        row = repository.get(session, created.front_matter.id)

    assert row is not None
    assert row.processing_status == "needs_recovery"
    assert row.recovery_required is True
    assert row.edited_by == "user"
    repaired = artifacts.read_markdown("knowledge/broken.md")
    assert repaired.front_matter.id == created.front_matter.id
    assert repaired.front_matter.recovery_required is True
    assert repaired.body == "# Broken\n\nBody stays.\n"
    revisions = list((workspace.root / ".revisions" / created.front_matter.id).glob("*.md"))
    assert len(revisions) == 1
    assert "kind: [not-valid" in revisions[0].read_text(encoding="utf-8")


def test_rebuild_projection_marks_unmatched_plain_markdown_for_recovery(
    client, tmp_path: Path
) -> None:
    workspace, artifacts, repository = _services(tmp_path)
    path = workspace.resolve_path("knowledge/manual-note.md")
    path.write_text("# Manual note\n\nNo metadata yet.\n", encoding="utf-8")

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        first_rows = repository.list(session)

    assert len(first_rows) == 1
    row = first_rows[0]
    assert row.kind == "knowledge"
    assert row.processing_status == "needs_recovery"
    assert row.index_status == "stale"
    assert row.recovery_required is True
    assert row.origin == "human"
    assert row.edited_by == "user"
    repaired = artifacts.read_markdown("knowledge/manual-note.md")
    assert repaired.front_matter.id == row.id
    assert repaired.body == "# Manual note\n\nNo metadata yet.\n"

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        repeated_rows = repository.list(session)

    assert len(repeated_rows) == 1
    assert repeated_rows[0].id == row.id
    assert repeated_rows[0].processing_status == "needs_recovery"


def test_rebuild_projection_ignores_workspace_manifest_and_revisions(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _services(tmp_path)
    created = artifacts.create_markdown("knowledge/topic.md", kind="knowledge", body="# Topic\n")
    artifacts.update_sections(
        "knowledge/topic.md",
        expected_revision=created.front_matter.revision,
        sections={"内容": "新内容"},
    )

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts)
        rows = repository.list(session)

    assert [row.relative_path for row in rows] == ["knowledge/topic.md"]
