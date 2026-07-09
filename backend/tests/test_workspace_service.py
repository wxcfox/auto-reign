from pathlib import Path

import pytest

from app.services.artifact_service import ArtifactService
from app.services.workspace_service import UnsafeWorkspacePath, WorkspaceService


def test_initialize_creates_fixed_workspace_tree_and_manifest(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "workspace")

    root = service.initialize(language="zh-CN")

    assert root == (tmp_path / "workspace").resolve()
    expected_directories = {
        "raw",
        "extracted",
        "profile",
        "knowledge",
        "questions",
        "practice",
        "review",
        "state",
        "reports",
        ".revisions",
    }
    assert expected_directories <= {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()
    }
    assert not (root / "inbox").exists()
    assert not (root / "sources").exists()
    workspace_manifest = (root / "workspace.md").read_text(encoding="utf-8")
    assert "schema_version: 1" in workspace_manifest
    assert "language: zh-CN" in workspace_manifest
    assert "Auto Reign" in workspace_manifest
    user_manifest = (root / "manifest.md").read_text(encoding="utf-8")
    assert "kind: manifest" in user_manifest
    assert "## 推荐阅读顺序" in user_manifest
    assert "manifest 是用户可调的工作区地图" in user_manifest


def test_initialize_syncs_unmodified_manifest_from_runtime_default(tmp_path: Path) -> None:
    default_manifest = tmp_path / "data" / "default_manifest.md"
    default_manifest.parent.mkdir()
    default_manifest.write_text("# 默认清单 A\n\n初始阅读顺序。\n", encoding="utf-8")
    service = WorkspaceService(tmp_path / "workspace", default_manifest_path=default_manifest)
    root = service.initialize(language="zh-CN")

    first = ArtifactService(service).read_markdown("manifest.md")
    assert first.front_matter.kind == "manifest"
    assert first.front_matter.edited_by == "system"
    assert first.body == "# 默认清单 A\n\n初始阅读顺序。\n"

    default_manifest.write_text("# 默认清单 B\n\n后台调整后的阅读顺序。\n", encoding="utf-8")
    service.initialize(language="zh-CN")

    updated = ArtifactService(service).read_markdown("manifest.md")
    assert updated.front_matter.id == first.front_matter.id
    assert updated.front_matter.revision == first.front_matter.revision + 1
    assert updated.body == "# 默认清单 B\n\n后台调整后的阅读顺序。\n"
    assert (root / ".revisions" / first.front_matter.id).exists()


def test_initialize_does_not_overwrite_user_modified_manifest(tmp_path: Path) -> None:
    default_manifest = tmp_path / "data" / "default_manifest.md"
    default_manifest.parent.mkdir()
    default_manifest.write_text("# 默认清单 A\n", encoding="utf-8")
    service = WorkspaceService(tmp_path / "workspace", default_manifest_path=default_manifest)
    service.initialize(language="zh-CN")
    artifacts = ArtifactService(service)
    current = artifacts.read_markdown("manifest.md")
    artifacts.replace_body(
        "manifest.md",
        expected_revision=current.front_matter.revision,
        body="# 我的清单\n\n我自己的阅读顺序。\n",
        edited_by="user",
    )

    default_manifest.write_text("# 默认清单 B\n", encoding="utf-8")
    service.initialize(language="zh-CN")

    preserved = artifacts.read_markdown("manifest.md")
    assert preserved.front_matter.edited_by == "user"
    assert preserved.body == "# 我的清单\n\n我自己的阅读顺序。\n"


def test_resolve_rejects_absolute_and_parent_escape(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "workspace")
    service.initialize()

    with pytest.raises(UnsafeWorkspacePath):
        service.resolve_path("../outside.md")
    with pytest.raises(UnsafeWorkspacePath):
        service.resolve_path(tmp_path / "outside.md")


def test_resolve_rejects_symlink_parent_escape(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "workspace")
    root = service.initialize()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "knowledge" / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeWorkspacePath):
        service.resolve_path("knowledge/escape/topic.md")


def test_resolve_accepts_safe_nonexistent_path(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "workspace")
    root = service.initialize()

    assert service.resolve_path("knowledge/python.md") == root / "knowledge/python.md"
