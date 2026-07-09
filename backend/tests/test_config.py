from importlib.resources import files
from pathlib import Path

from app.core.config import Settings


def test_settings_exposes_workspace_dir_without_creating_uploads(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    assert settings.workspace_dir == tmp_path / "data" / "workspace"

    settings.ensure_data_dirs()

    assert settings.data_dir.exists()
    assert not (settings.data_dir / "uploads").exists()


def test_settings_seeds_default_manifest_without_overwriting_admin_changes(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    assert settings.default_manifest_path == tmp_path / "data" / "default_manifest.md"
    settings.ensure_data_dirs()

    seeded = settings.default_manifest_path.read_text(encoding="utf-8")
    assert "# 工作区清单" in seeded
    settings.default_manifest_path.write_text("# 管理员默认清单\n", encoding="utf-8")
    settings.ensure_data_dirs()

    assert settings.default_manifest_path.read_text(encoding="utf-8") == "# 管理员默认清单\n"


def test_packaged_manifest_seed_uses_example_filename() -> None:
    templates = files("app.templates")

    assert templates.joinpath("default_manifest.example.md").is_file()
    assert not templates.joinpath("default_manifest.md").is_file()


def test_settings_generates_stable_local_jwt_secret(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", jwt_secret_key=None)

    secret = settings.resolve_jwt_secret_key()
    reloaded = Settings(data_dir=tmp_path / "data", jwt_secret_key=None)

    assert secret == reloaded.resolve_jwt_secret_key()
    assert secret != "auto-reign-local-dev-secret-change-me"
    assert len(secret) >= 32
    assert (tmp_path / "data" / ".secrets" / "jwt_secret").read_text(
        encoding="utf-8"
    ).strip() == secret
