import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from app.db.models import Base, Resource, User
from app.db.session import make_session_factory, session_scope
from app.repositories.user_repository import UserRepository
from app.services.bootstrap_service import BootstrapService, bootstrap_application


INIT_DATA_DIR = Path(__file__).parents[2] / "init_data"


class _MySQLRaceUserRepository(UserRepository):
    def __init__(self) -> None:
        self.initial_lookup_barrier = Barrier(2)
        self.lock = Lock()
        self.initial_lookup_sessions: list[Session] = []
        self.verification_sessions: list[Session] = []

    def get_by_username(self, session: Session, username: str) -> User | None:
        user = super().get_by_username(session, username)
        if user is None:
            with self.lock:
                self.initial_lookup_sessions.append(session)
            self.initial_lookup_barrier.wait(timeout=30)
        else:
            with self.lock:
                self.verification_sessions.append(session)
        return user


def _normalized_host(host: str | None) -> str:
    normalized = (host or "").casefold().rstrip(".")
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return "loopback"
    return normalized


def _database_identity(url: URL) -> tuple[str, int, str | None]:
    return (
        _normalized_host(url.host),
        url.port or 3306,
        url.database.casefold() if url.database else None,
    )


def _disposable_mysql_url() -> URL:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires RUN_MYSQL_INTEGRATION=1")
    explicit_url = os.environ.get("MYSQL_BOOTSTRAP_RACE_DATABASE_URL")
    if not explicit_url:
        pytest.fail(
            "RUN_MYSQL_INTEGRATION=1 requires an explicit disposable "
            "MYSQL_BOOTSTRAP_RACE_DATABASE_URL"
        )

    try:
        parsed_url = make_url(explicit_url)
    except ArgumentError:
        pytest.fail("MYSQL_BOOTSTRAP_RACE_DATABASE_URL is not a valid database URL")
    if not parsed_url.drivername.startswith("mysql"):
        pytest.fail("MYSQL_BOOTSTRAP_RACE_DATABASE_URL must use a MySQL driver")
    if not parsed_url.database:
        pytest.fail("MYSQL_BOOTSTRAP_RACE_DATABASE_URL must name a disposable database")
    if not parsed_url.database.casefold().endswith("_test"):
        pytest.fail(
            "MYSQL_BOOTSTRAP_RACE_DATABASE_URL database name must end with _test"
        )
    if parsed_url.database.casefold() in {
        "information_schema",
        "mysql",
        "performance_schema",
        "sys",
    }:
        pytest.fail("MYSQL_BOOTSTRAP_RACE_DATABASE_URL must not name a system database")

    default_url = os.environ.get("DATABASE_URL")
    if default_url:
        try:
            parsed_default_url = make_url(default_url)
            disposable_identity = _database_identity(parsed_url)
            default_identity = _database_identity(parsed_default_url)
            if disposable_identity == default_identity:
                pytest.fail(
                    "MYSQL_BOOTSTRAP_RACE_DATABASE_URL must differ from DATABASE_URL"
                )
        except ArgumentError as error:
            pytest.fail(
                "cannot prove the bootstrap race database is disposable because "
                "DATABASE_URL is invalid"
            )
            raise AssertionError from error
    return parsed_url


def test_integration_flag_requires_explicit_bootstrap_url(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.delenv("MYSQL_BOOTSTRAP_RACE_DATABASE_URL", raising=False)

    with pytest.raises(pytest.fail.Exception, match="explicit disposable"):
        _disposable_mysql_url()


def test_bootstrap_database_guard_rejects_non_test_schema(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.setenv(
        "MYSQL_BOOTSTRAP_RACE_DATABASE_URL",
        "mysql+pymysql://user:pass@127.0.0.1/production",
    )

    with pytest.raises(pytest.fail.Exception, match="end with _test"):
        _disposable_mysql_url()


def test_bootstrap_database_guard_rejects_loopback_alias(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.setenv(
        "MYSQL_BOOTSTRAP_RACE_DATABASE_URL",
        "mysql+pymysql://user:pass@LOCALHOST./AUTO_REIGN_TEST",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "mysql+pymysql://user:pass@127.0.0.1:3306/auto_reign_test",
    )

    with pytest.raises(pytest.fail.Exception, match="must differ"):
        _disposable_mysql_url()


def test_concurrent_bootstrap_on_disposable_mysql_database() -> None:
    engine = create_engine(_disposable_mysql_url(), pool_pre_ping=True)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        session_factory = make_session_factory(engine)
        users = _MySQLRaceUserRepository()
        service = BootstrapService(users=users)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _index: bootstrap_application(
                        session_factory,
                        init_data_dir=INIT_DATA_DIR,
                        service=service,
                    ),
                    range(2),
                )
            )

        assert results == [None, None]
        assert len(users.initial_lookup_sessions) == 2
        assert len(users.verification_sessions) == 1
        assert all(
            users.verification_sessions[0] is not initial_session
            for initial_session in users.initial_lookup_sessions
        )
        with session_scope(session_factory) as session:
            assert session.scalar(select(func.count(User.id))) == 1
            assert session.scalar(select(func.count(Resource.id))) == 2
    finally:
        try:
            Base.metadata.drop_all(engine)
        finally:
            engine.dispose()
