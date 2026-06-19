from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.models import router as models_router


def create_app() -> FastAPI:
    app = FastAPI(title="Auto Reign API")
    app.include_router(health_router)
    app.include_router(models_router)
    return app


app = create_app()
