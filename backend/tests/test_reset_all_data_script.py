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
        root / "custom-objects" / "users" / "1" / "attachment.txt",
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
                "OBJECT_STORE_LOCAL_ROOT=./custom-objects",
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
        "custom-objects",
        "data",
        "logs",
    ]
    assert not (root / "data").exists()
    assert not (root / "custom-data").exists()
    assert not (root / "custom-objects").exists()
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


def test_reset_all_data_refuses_local_object_root_outside_repo_before_deleting(
    tmp_path: Path,
) -> None:
    module = load_reset_module()
    outside = tmp_path.parent / "outside-auto-reign-objects"
    local_data = tmp_path / "data" / "keep.txt"
    local_data.parent.mkdir(parents=True)
    local_data.write_text("keep", encoding="utf-8")
    (tmp_path / ".env").write_text(
        f"OBJECT_STORE_LOCAL_ROOT={outside}\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    with pytest.raises(ValueError, match="outside repository"):
        module.reset_all_data(
            root=tmp_path,
            yes=True,
            dry_run=False,
            skip_docker=False,
            command_runner=lambda command: commands.append(command),
        )

    assert commands == []
    assert local_data.read_text(encoding="utf-8") == "keep"


def test_reset_all_data_refuses_symlinked_object_root_before_deleting(
    tmp_path: Path,
) -> None:
    module = load_reset_module()
    outside = tmp_path.parent / f"outside-objects-{tmp_path.name}"
    outside.mkdir()
    local_data = tmp_path / "data" / "keep.txt"
    local_data.parent.mkdir(parents=True)
    local_data.write_text("keep", encoding="utf-8")
    (tmp_path / "object-link").symlink_to(outside, target_is_directory=True)
    (tmp_path / ".env").write_text(
        "OBJECT_STORE_LOCAL_ROOT=./object-link\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    try:
        with pytest.raises(ValueError, match="outside repository"):
            module.reset_all_data(
                root=tmp_path,
                yes=True,
                dry_run=False,
                skip_docker=False,
                command_runner=lambda command: commands.append(command),
            )

        assert commands == []
        assert local_data.exists()
        assert (tmp_path / "object-link").is_symlink()
    finally:
        outside.rmdir()


def test_remote_s3_configuration_is_never_a_reset_candidate(tmp_path: Path) -> None:
    module = load_reset_module()
    for path in (
        tmp_path / "data" / "local.txt",
        tmp_path / "remote-bucket" / "keep.txt",
        tmp_path / "remote-prefix" / "keep.txt",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("runtime", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DATA_DIR=./data",
                "OBJECT_STORE_BACKEND=s3",
                "S3_BUCKET=./remote-bucket",
                "S3_ENDPOINT_URL=https://secret-endpoint.example.invalid",
                "S3_ACCESS_KEY_ID=secret-id",
                "S3_SECRET_ACCESS_KEY=secret-key",
                "S3_KEY_PREFIX=./remote-prefix",
            ]
        ),
        encoding="utf-8",
    )

    commands: list[list[str]] = []
    result = module.reset_all_data(
        root=tmp_path,
        yes=True,
        dry_run=False,
        skip_docker=True,
        command_runner=lambda command: commands.append(command),
    )

    assert commands == []
    assert [path.relative_to(tmp_path).as_posix() for path in result.removed_paths] == [
        "data"
    ]
    assert (tmp_path / "remote-bucket" / "keep.txt").exists()
    assert (tmp_path / "remote-prefix" / "keep.txt").exists()
    assert module.ENV_PATH_KEYS == ("DATA_DIR", "OBJECT_STORE_LOCAL_ROOT")
    assert "Remote S3/OSS objects are never purged" in module.REMOTE_OBJECT_WARNING


def test_reset_dry_run_does_not_remove_local_paths_or_run_commands(
    tmp_path: Path,
) -> None:
    module = load_reset_module()
    local_data = tmp_path / "data" / "keep.txt"
    local_data.parent.mkdir(parents=True)
    local_data.write_text("keep", encoding="utf-8")
    commands: list[list[str]] = []

    result = module.reset_all_data(
        root=tmp_path,
        yes=False,
        dry_run=True,
        skip_docker=False,
        command_runner=lambda command: commands.append(command),
    )

    assert commands == []
    assert local_data.exists()
    assert tmp_path / "data" in result.removed_paths


def test_reset_help_and_summary_explicitly_include_redis(tmp_path: Path, capsys) -> None:
    module = load_reset_module()
    monkey_root = tmp_path

    result = module.reset_all_data(
        root=monkey_root,
        yes=False,
        dry_run=True,
        skip_docker=False,
        command_runner=lambda _command: None,
    )

    assert result.removed_paths == []
    module.main(["--dry-run"])
    output = capsys.readouterr().out
    assert "Redis" in output
