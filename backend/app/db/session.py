from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def create_engine_for_settings(settings: Settings) -> Engine:
    backend = make_url(settings.database_url).get_backend_name()
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if backend == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
    elif backend == "mysql":
        kwargs["isolation_level"] = "READ COMMITTED"
    return create_engine(settings.database_url, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
