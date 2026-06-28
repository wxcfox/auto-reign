from pathlib import Path

from app.core.config import Settings


def test_settings_exposes_workspace_dir_without_creating_uploads(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    assert settings.workspace_dir == tmp_path / "data" / "workspace"

    settings.ensure_data_dirs()

    assert settings.data_dir.exists()
    assert not (settings.data_dir / "uploads").exists()
