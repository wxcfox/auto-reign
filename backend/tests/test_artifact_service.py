import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.artifact_service import ArtifactConflict, ArtifactService
from app.services.workspace_service import WorkspaceService


@pytest.fixture
def services(tmp_path: Path) -> tuple[WorkspaceService, ArtifactService]:
    workspace = WorkspaceService(tmp_path / "workspace")
    workspace.initialize()
    return workspace, ArtifactService(workspace, revisions_retained=20)


def test_create_and_parse_managed_markdown(
    services: tuple[WorkspaceService, ArtifactService],
) -> None:
    workspace, artifacts = services
    document = artifacts.create_markdown(
        "knowledge/python.md",
        kind="knowledge",
        body="# Python\n\n## 核心概念\n\n解释器。\n",
        source_refs=["source:resume-1"],
        origin="human",
    )

    parsed = artifacts.read_markdown("knowledge/python.md")

    assert parsed.front_matter.id == document.front_matter.id
    assert parsed.front_matter.kind == "knowledge"
    assert parsed.front_matter.revision == 1
    assert parsed.front_matter.source_refs == ["source:resume-1"]
    assert parsed.front_matter.created_at.tzinfo is not None
    assert parsed.body == "# Python\n\n## 核心概念\n\n解释器。\n"
    assert workspace.resolve_path("knowledge/python.md").read_text().startswith("---\n")


def test_update_sections_preserves_unknown_sections_and_creates_revision(
    services: tuple[WorkspaceService, ArtifactService],
) -> None:
    workspace, artifacts = services
    created = artifacts.create_markdown(
        "profile/candidate.md",
        kind="candidate_profile",
        body=(
            "# 候选人画像\n\n"
            "开场说明保持原样。\n\n"
            "## 基本背景\n\n旧背景。\n\n"
            "## 用户自定义\n\n不要改这一段。\n"
        ),
    )

    updated = artifacts.update_sections(
        "profile/candidate.md",
        expected_revision=created.front_matter.revision,
        sections={"基本背景": "新背景。"},
        edited_by="system",
    )

    assert updated.front_matter.revision == 2
    assert "## 基本背景\n\n新背景。" in updated.body
    assert "开场说明保持原样。" in updated.body
    assert "## 用户自定义\n\n不要改这一段。" in updated.body
    revisions = list((workspace.root / ".revisions" / created.front_matter.id).glob("*.md"))
    assert len(revisions) == 1
    assert "旧背景。" in revisions[0].read_text(encoding="utf-8")


def test_update_rejects_stale_revision_without_changing_file(
    services: tuple[WorkspaceService, ArtifactService],
) -> None:
    workspace, artifacts = services
    artifacts.create_markdown(
        "state/plan.md",
        kind="plan",
        body="# 当前计划\n\n## 优先任务\n\n1. 第一项\n",
    )
    before = workspace.resolve_path("state/plan.md").read_bytes()

    with pytest.raises(ArtifactConflict):
        artifacts.update_sections(
            "state/plan.md",
            expected_revision=0,
            sections={"优先任务": "1. 错误覆盖"},
        )

    assert workspace.resolve_path("state/plan.md").read_bytes() == before


def test_source_bytes_are_immutable_and_have_a_json_sidecar(
    services: tuple[WorkspaceService, ArtifactService],
) -> None:
    workspace, artifacts = services
    content = b"exact original bytes\x00\xff"

    meta = artifacts.store_source(
        source_filename="../resume final.pdf",
        media_type="application/pdf",
        content=content,
        language="zh-CN",
    )

    source_path = workspace.resolve_path(meta.relative_path)
    assert source_path.read_bytes() == content
    assert source_path.parent == workspace.root / "sources" / "documents"
    assert ".." not in source_path.name
    sidecar = json.loads(source_path.with_name(f"{source_path.name}.meta.json").read_text())
    assert sidecar["artifact_id"] == meta.artifact_id
    assert sidecar["source_filename"] == "../resume final.pdf"
    assert sidecar["content_hash"] == meta.content_hash
    assert sidecar["uploaded_at"].endswith("Z")

    with pytest.raises(FileExistsError):
        artifacts.store_source(
            source_filename="resume.pdf",
            media_type="application/pdf",
            content=b"changed",
            artifact_id=meta.artifact_id,
        )
    assert source_path.read_bytes() == content


def test_revision_retention_uses_time_not_lexical_revision_order(
    services: tuple[WorkspaceService, ArtifactService],
) -> None:
    workspace, artifacts = services
    created = artifacts.create_markdown(
        "knowledge/retention.md",
        kind="knowledge",
        body="# Retention\n\n## 内容\n\n0\n",
    )
    revision = created.front_matter.revision
    for value in range(1, 23):
        updated = artifacts.update_sections(
            "knowledge/retention.md",
            expected_revision=revision,
            sections={"内容": str(value)},
        )
        revision = updated.front_matter.revision

    revision_dir = workspace.root / ".revisions" / created.front_matter.id
    retained = list(revision_dir.glob("*.md"))
    assert len(retained) == 20
    retained_revisions = {
        artifacts.parse_markdown(path.read_text(encoding="utf-8")).front_matter.revision
        for path in retained
    }
    assert retained_revisions == set(range(3, 23))


def test_atomic_write_does_not_replace_target_when_replace_fails(
    services: tuple[WorkspaceService, ArtifactService], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, artifacts = services
    path = workspace.resolve_path("knowledge/atomic.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old", encoding="utf-8")

    def fail_replace(source: str | Path, target: str | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        artifacts.atomic_write_bytes(path, b"new")

    assert path.read_text(encoding="utf-8") == "old"
    assert not list(path.parent.glob(".atomic.md.*.tmp"))


def test_front_matter_serializes_utc_as_z(
    services: tuple[WorkspaceService, ArtifactService],
) -> None:
    _, artifacts = services
    created = artifacts.create_markdown(
        "reports/sample.md",
        kind="report",
        body="# 报告\n",
        now=datetime(2026, 6, 22, 2, 0, tzinfo=UTC),
    )

    assert "created_at: '2026-06-22T02:00:00Z'" in created.raw
