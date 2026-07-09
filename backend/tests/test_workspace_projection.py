from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import models
from app.repositories.artifact_repository import ArtifactRepository
from app.schemas.workspace import SourceMeta
from app.services.artifact_metadata import (
    artifact_edited_by,
    artifact_index_status,
    artifact_media_type,
    artifact_origin,
    artifact_processing_status,
    artifact_recovery_required,
    artifact_source_filename,
    artifact_source_refs,
)
from app.services.artifact_service import ArtifactService
from app.services.workspace_service import WorkspaceService

USER_ID = 1


def _session(client) -> Session:
    return client.app.state.session_factory()


def _services(tmp_path: Path) -> tuple[WorkspaceService, ArtifactService, ArtifactRepository]:
    workspace = WorkspaceService(tmp_path / "workspace")
    workspace.initialize()
    return workspace, ArtifactService(workspace), ArtifactRepository()


def _create_user(session: Session) -> models.User:
    existing = session.get(models.User, USER_ID)
    if existing is not None:
        return existing
    user = models.User(
        username="alice",
        password_hash="hash",
        settings_json={
            "schema_version": 1,
            "language": "zh-CN",
            "active_collection": "auto_reign_user_1",
        },
    )
    session.add(user)
    session.flush()
    assert user.id == USER_ID
    return user


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
        _create_user(session)
        workspace.rebuild_projection(session, repository, artifacts, user_id=USER_ID)
        rows = {
            artifact.relative_path: artifact
            for artifact in repository.list(session, user_id=USER_ID)
        }

    assert rows[source.relative_path].id == source.artifact_id
    assert rows[source.relative_path].user_id == USER_ID
    assert rows[source.relative_path].kind == "source"
    assert artifact_source_filename(rows[source.relative_path]) == "resume.md"
    assert artifact_media_type(rows[source.relative_path]) == "text/markdown"
    assert artifact_origin(rows[source.relative_path]) == "human"
    assert rows["knowledge/python.md"].id == knowledge.front_matter.id
    assert rows["knowledge/python.md"].user_id == USER_ID
    assert artifact_source_refs(rows["knowledge/python.md"]) == [f"source:{source.artifact_id}"]
    assert artifact_processing_status(rows["knowledge/python.md"]) == "completed"


def test_rebuild_projection_removes_ghost_artifact(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _services(tmp_path)
    with _session(client) as session:
        _create_user(session)
        artifact = models.Artifact(
            id="ghost",
            user_id=USER_ID,
            kind="knowledge",
            relative_path="knowledge/missing.md",
            content_hash="old",
        )
        session.add(artifact)
        session.flush()
        session.commit()

    with _session(client) as session:
        _create_user(session)
        workspace.rebuild_projection(session, repository, artifacts, user_id=USER_ID)
        assert repository.get(session, user_id=USER_ID, artifact_id="ghost") is None


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
        _create_user(session)
        workspace.rebuild_projection(session, repository, artifacts, user_id=USER_ID)
        session.commit()

    broken_path = workspace.resolve_path("knowledge/broken.md")
    broken_path.write_text("---\nkind: [not-valid\n---\n# Broken\n\nBody stays.\n", encoding="utf-8")

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts, user_id=USER_ID)
        row = repository.get(session, user_id=USER_ID, artifact_id=created.front_matter.id)

    assert row is not None
    assert artifact_processing_status(row) == "needs_recovery"
    assert artifact_recovery_required(row) is True
    assert artifact_edited_by(row) == "user"
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
        _create_user(session)
        workspace.rebuild_projection(session, repository, artifacts, user_id=USER_ID)
        first_rows = repository.list(session, user_id=USER_ID)

    assert {row.relative_path for row in first_rows} == {
        "knowledge/manual-note.md",
        "manifest.md",
    }
    row = next(row for row in first_rows if row.relative_path == "knowledge/manual-note.md")
    assert row.kind == "knowledge"
    assert artifact_processing_status(row) == "needs_recovery"
    assert artifact_index_status(row) == "stale"
    assert artifact_recovery_required(row) is True
    assert artifact_origin(row) == "human"
    assert artifact_edited_by(row) == "user"
    repaired = artifacts.read_markdown("knowledge/manual-note.md")
    assert repaired.front_matter.id == row.id
    assert repaired.body == "# Manual note\n\nNo metadata yet.\n"

    with _session(client) as session:
        workspace.rebuild_projection(session, repository, artifacts, user_id=USER_ID)
        repeated_rows = repository.list(session, user_id=USER_ID)

    repeated_row = next(
        repeated for repeated in repeated_rows if repeated.relative_path == "knowledge/manual-note.md"
    )
    assert repeated_row.id == row.id
    assert artifact_processing_status(repeated_row) == "needs_recovery"


def test_rebuild_projection_ignores_legacy_sources_paths(client, tmp_path: Path) -> None:
    workspace, artifacts, repository = _services(tmp_path)
    legacy_document = workspace.resolve_path("sources/documents/legacy-resume.md")
    legacy_document.parent.mkdir(parents=True)
    legacy_document.write_text("# Legacy Resume\n", encoding="utf-8")
    legacy_meta = SourceMeta(
        artifact_id="legacy-source",
        source_filename="legacy-resume.md",
        media_type="text/markdown",
        size_bytes=legacy_document.stat().st_size,
        content_hash="legacy-hash",
        uploaded_at=datetime(2026, 7, 9, tzinfo=UTC),
        relative_path="sources/documents/legacy-resume.md",
        source_type="upload",
    )
    legacy_document.with_name("legacy-resume.md.meta.json").write_text(
        legacy_meta.model_dump_json(), encoding="utf-8"
    )
    workspace.resolve_path("sources/interviews").mkdir(parents=True)
    artifacts.create_markdown(
        "sources/interviews/legacy-interview.md",
        kind="interview_record",
        body="# Legacy Interview\n",
    )
    workspace.resolve_path("sources/extracted").mkdir(parents=True)
    artifacts.create_markdown(
        "sources/extracted/legacy-extracted.md",
        kind="extracted",
        body="# Legacy Extracted\n",
    )

    with _session(client) as session:
        _create_user(session)
        workspace.rebuild_projection(session, repository, artifacts, user_id=USER_ID)
        rows = repository.list(session, user_id=USER_ID)

    assert [row.relative_path for row in rows] == ["manifest.md"]


def test_rebuild_projection_tracks_manifest_and_ignores_workspace_manifest_and_revisions(
    client, tmp_path: Path
) -> None:
    workspace, artifacts, repository = _services(tmp_path)
    created = artifacts.create_markdown("knowledge/topic.md", kind="knowledge", body="# Topic\n")
    artifacts.update_sections(
        "knowledge/topic.md",
        expected_revision=created.front_matter.revision,
        sections={"内容": "新内容"},
    )

    with _session(client) as session:
        _create_user(session)
        workspace.rebuild_projection(session, repository, artifacts, user_id=USER_ID)
        rows = repository.list(session, user_id=USER_ID)

    assert [row.relative_path for row in rows] == ["knowledge/topic.md", "manifest.md"]
    manifest = next(row for row in rows if row.relative_path == "manifest.md")
    assert manifest.kind == "manifest"
