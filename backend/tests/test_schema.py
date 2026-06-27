from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
APPLICATION_TABLES = {
    "artifacts",
    "interview_configs",
    "interview_sessions",
    "interview_turns",
    "processing_jobs",
    "reports",
    "workspace_settings",
}


def test_migration_creates_and_drops_required_schema(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        config = Config(ALEMBIC_INI)
        command.upgrade(config, "head")
        command.check(config)

        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == APPLICATION_TABLES | {"alembic_version"}

        turn_columns = {column["name"] for column in inspector.get_columns("interview_turns")}
        assert {
            "follow_up_feedback",
            "follow_up_missing_points",
            "follow_up_weaknesses",
            "follow_up_review_suggestions",
            "better_answer",
            "mastery_change",
            "should_write_weakness",
            "should_write_high_frequency",
            "tested_points",
            "follow_up_better_answer",
            "follow_up_mastery_change",
            "follow_up_should_write_weakness",
            "follow_up_should_write_high_frequency",
            "follow_up_tested_points",
        }.issubset(turn_columns)
        config_columns = {column["name"] for column in inspector.get_columns("interview_configs")}
        assert "language" in config_columns

        artifact_columns = {column["name"] for column in inspector.get_columns("artifacts")}
        assert {
            "kind",
            "relative_path",
            "source_refs",
            "evidence_refs",
            "processing_status",
            "index_status",
            "recovery_required",
        }.issubset(artifact_columns)

        turn_foreign_key = next(
            foreign_key
            for foreign_key in inspector.get_foreign_keys("interview_turns")
            if foreign_key["constrained_columns"] == ["session_id"]
        )
        assert turn_foreign_key["referred_table"] == "interview_sessions"
        assert turn_foreign_key["referred_columns"] == ["id"]
        assert turn_foreign_key["options"]["ondelete"] == "CASCADE"

        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_migration_removes_legacy_absolute_report_paths(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        config = Config(ALEMBIC_INI)
        command.upgrade(config, "20260627_0007")
        timestamp = "2026-06-27 00:00:00"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO interview_configs "
                    "(id, target_company, target_role, job_description, extra_prompt, language, "
                    "mode, chat_model_provider, chat_model, target_rounds, is_last_used, updated_at) "
                    "VALUES "
                    "('config-1', '', '', '', '', 'zh-CN', 'comprehensive', "
                    "'openai', 'gpt-4.1-mini', 1, 0, :timestamp)"
                ),
                {"timestamp": timestamp},
            )
            connection.execute(
                text(
                    "INSERT INTO interview_sessions "
                    "(id, config_id, status, current_round, started_at, ended_at, report_path) "
                    "VALUES "
                    "('session-legacy', 'config-1', 'completed', 1, :timestamp, :timestamp, "
                    ":legacy_path), "
                    "('session-current', 'config-1', 'completed', 1, :timestamp, :timestamp, "
                    "'reports/current.md')"
                ),
                {"timestamp": timestamp, "legacy_path": str(tmp_path / "reports" / "legacy.md")},
            )
            connection.execute(
                text(
                    "INSERT INTO reports "
                    "(id, session_id, report_path, summary, weaknesses, created_at) "
                    "VALUES "
                    "('report-legacy', 'session-legacy', :legacy_path, 'old', '[]', :timestamp), "
                    "('report-current', 'session-current', 'reports/current.md', 'current', '[]', "
                    ":timestamp)"
                ),
                {"timestamp": timestamp, "legacy_path": str(tmp_path / "reports" / "legacy.md")},
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            session_paths = dict(
                connection.execute(
                    text("SELECT id, report_path FROM interview_sessions ORDER BY id")
                ).all()
            )
            report_paths = dict(
                connection.execute(text("SELECT id, report_path FROM reports ORDER BY id")).all()
            )

        assert session_paths["session-legacy"] is None
        assert session_paths["session-current"] == "reports/current.md"
        assert report_paths == {"report-current": "reports/current.md"}
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_alembic_does_not_create_data_directories(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "read-only-data"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'side-effect.db'}")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    get_settings.cache_clear()

    try:
        assert not data_dir.exists()
        command.upgrade(Config(ALEMBIC_INI), "head")
        assert not data_dir.exists()
    finally:
        get_settings.cache_clear()


def test_mysql_offline_migration_does_not_set_json_defaults(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign")
    get_settings.cache_clear()

    try:
        command.upgrade(Config(ALEMBIC_INI), "head", sql=True)
        stdout = capsys.readouterr().out
    finally:
        get_settings.cache_clear()

    assert "follow_up_missing_points JSON NOT NULL" in stdout
    assert "follow_up_weaknesses JSON NOT NULL" in stdout
    assert "follow_up_review_suggestions JSON NOT NULL" in stdout
    assert "follow_up_tested_points JSON NOT NULL" in stdout
    assert "better_answer TEXT NOT NULL DEFAULT" not in stdout
    assert "follow_up_better_answer TEXT NOT NULL DEFAULT" not in stdout
    assert "JSON NOT NULL DEFAULT '[]'" not in stdout
    assert "relative_path VARCHAR(1024)" not in stdout
    assert "relative_path VARCHAR(512)" in stdout
