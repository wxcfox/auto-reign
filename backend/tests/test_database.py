from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import main as main_module
from app.core.config import Settings
from app.db.session import create_engine_for_settings

STORAGE_ENVIRONMENT_VARIABLES = (
    "DATABASE_URL",
    "QDRANT_URL",
    "QDRANT_COLLECTION",
)


def test_settings_exposes_database_and_qdrant_configuration(monkeypatch) -> None:
    for variable in STORAGE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    settings = Settings(_env_file=None)

    assert settings.database_url == (
        "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign"
    )
    assert settings.qdrant_url == "http://127.0.0.1:16333"
    assert settings.qdrant_collection == "auto_reign_default"


def test_settings_does_not_expose_legacy_storage_configuration(monkeypatch) -> None:
    for variable in STORAGE_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    settings = Settings(_env_file=None)

    assert not hasattr(settings, "sqlite_path")
    assert not hasattr(settings, "chroma_dir")
    assert not hasattr(settings, "default_collection")


def test_create_engine_uses_database_url_and_pool_pre_ping() -> None:
    settings = Settings(
        _env_file=None,
        database_url="mysql+pymysql://user:password@database.example/auto_reign",
    )

    engine = create_engine_for_settings(settings)

    try:
        assert engine.url.drivername == "mysql+pymysql"
        assert engine.url.host == "database.example"
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()


def test_app_shutdown_disposes_engine(monkeypatch) -> None:
    engine = Mock()
    monkeypatch.setattr(main_module, "create_engine_for_settings", lambda _settings: engine)

    app = main_module.create_app()
    with TestClient(app):
        pass

    engine.dispose.assert_called_once_with()
