from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def load_reset_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "reset_all_data.py"
    spec = importlib.util.spec_from_file_location("reset_all_data_script", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_reset_all_data_removes_runtime_state_and_keeps_configuration(tmp_path: Path) -> None:
    module = load_reset_module()
    root = tmp_path
    for path in [
        root / "data" / "workspace" / "knowledge.md",
        root / ".pids" / "backend.json",
        root / "logs" / "backend.log",
        root / "custom-data" / "upload.txt",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("runtime", encoding="utf-8")
    for path in [
        root / "backend" / "data" / "app.db",
        root / "legacy.sqlite3",
        root / "legacy-chroma" / "index.bin",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("legacy", encoding="utf-8")
    (root / ".env").write_text(
        "\n".join(
            [
                "DATA_DIR=./custom-data",
                "SQLITE_PATH=./legacy.sqlite3",
                "CHROMA_DIR=./legacy-chroma",
                "QWEN_API_KEY=local-secret",
            ]
        ),
        encoding="utf-8",
    )
    (root / ".env.example").write_text("DATA_DIR=./data\n", encoding="utf-8")
    commands: list[list[str]] = []

    result = module.reset_all_data(
        root=root,
        yes=True,
        dry_run=False,
        skip_docker=False,
        command_runner=lambda command: commands.append(command),
    )

    assert commands == [
        ["./start.sh", "--stop"],
        ["docker", "compose", "-p", "auto-reign", "down", "-v", "--remove-orphans"],
    ]
    assert sorted(path.relative_to(root).as_posix() for path in result.removed_paths) == [
        ".pids",
        "custom-data",
        "data",
        "logs",
    ]
    assert not (root / "data").exists()
    assert not (root / "custom-data").exists()
    assert (root / "backend" / "data" / "app.db").exists()
    assert (root / "legacy.sqlite3").exists()
    assert (root / "legacy-chroma" / "index.bin").exists()
    assert (root / ".env").read_text(encoding="utf-8").endswith("local-secret")


def test_reset_all_data_requires_explicit_confirmation(tmp_path: Path) -> None:
    module = load_reset_module()

    with pytest.raises(SystemExit, match="Refusing to reset data without --yes"):
        module.reset_all_data(
            root=tmp_path,
            yes=False,
            dry_run=False,
            skip_docker=True,
            command_runner=lambda _command: None,
        )


def test_reset_all_data_refuses_env_paths_outside_repo(tmp_path: Path) -> None:
    module = load_reset_module()
    outside = tmp_path.parent / "outside-auto-reign-data"
    (tmp_path / ".env").write_text(f"DATA_DIR={outside}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside repository"):
        module.reset_all_data(
            root=tmp_path,
            yes=True,
            dry_run=False,
            skip_docker=True,
            command_runner=lambda _command: None,
        )
