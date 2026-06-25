from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.api.health import router as health_router
from app.api.interviews import router as interviews_router
from app.api.memory import router as memory_router
from app.api.models import router as models_router
from app.api.reports import router as reports_router
from app.api.workspace import router as workspace_router
from app.core.config import get_settings
from app.db.session import create_engine_for_settings, make_session_factory, session_scope
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.artifact_service import ArtifactService
from app.services.index_service import IndexService
from app.services.workspace_service import WorkspaceService


def create_app() -> FastAPI:
    settings = get_settings()
    engine = create_engine_for_settings(settings)
    session_factory = make_session_factory(engine)
    workspace_service = WorkspaceService(settings.data_dir / "workspace")
    artifact_service = ArtifactService(workspace_service)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        workspace_service.initialize()
        try:
            with session_scope(session_factory) as session:
                WorkspaceSettingsRepository().get_or_create(session)
            IndexService().sweep_orphan_collections(session_factory)
        except SQLAlchemyError:
            pass
        except Exception:
            pass
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Auto Reign API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.session_factory = session_factory
    app.state.workspace_service = workspace_service
    app.state.artifact_service = artifact_service
    app.include_router(health_router)
    app.include_router(interviews_router)
    app.include_router(memory_router)
    app.include_router(models_router)
    app.include_router(reports_router)
    app.include_router(workspace_router)
    return app


app = create_app()
