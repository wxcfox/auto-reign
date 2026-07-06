from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.models import router as models_router
from app.core.config import get_settings
from app.db.session import create_engine_for_settings, make_session_factory


def create_app() -> FastAPI:
    settings = get_settings()
    engine = create_engine_for_settings(settings)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
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
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.include_router(auth_router)
    app.include_router(health_router)
    app.include_router(models_router)
    return app


app = create_app()
