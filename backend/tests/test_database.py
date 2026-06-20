from app.core.config import Settings
from app.db.session import create_engine_for_settings


def test_settings_exposes_database_and_qdrant_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url == (
        "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign"
    )
    assert settings.qdrant_url == "http://127.0.0.1:16333"
    assert settings.qdrant_collection == "auto_reign_default"


def test_settings_does_not_expose_legacy_storage_configuration() -> None:
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
