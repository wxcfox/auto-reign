from pathlib import Path

import pytest

from app.services.workspace_service import UnsafeWorkspacePath, WorkspaceService


def test_initialize_creates_fixed_workspace_tree_and_manifest(tmp_path: Path) -> None:
    service = WorkspaceService(tmp_path / "workspace")

    root = service.initialize(language="zh-CN")

    assert root == (tmp_path / "workspace").resolve()
    expected_directories = {
        "sources/documents",
        "sources/extracted",
        "profile",
        "knowledge",
        "practice",
        "state",
        "reports",
        "archive",
        ".revisions",
    }
    assert expected_directories <= {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()
    }
    manifest = (root / "workspace.md").read_text(encoding="utf-8")
    assert "schema_version: 1" in manifest
    assert "language: zh-CN" in manifest
    assert "Auto Reign" in manifest


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
