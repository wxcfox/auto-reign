from fastapi import FastAPI

from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.models import router as models_router
from app.api.rag import router as rag_router
from app.core.config import get_settings
from app.db.session import create_engine_for_settings, init_db, make_session_factory


def create_app() -> FastAPI:
    app = FastAPI(title="Auto Reign API")
    settings = get_settings()
    engine = create_engine_for_settings(settings)
    init_db(engine)
    app.state.session_factory = make_session_factory(engine)
    app.include_router(documents_router)
    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(rag_router)
    return app


app = create_app()
