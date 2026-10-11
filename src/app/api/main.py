"""FastAPI application. Run: uvicorn app.api.main:app --reload  ->  Swagger UI at /docs"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.config import Settings, get_settings
from app.api.db import GoldDB
from app.api.errors import add_error_handlers
from app.api.observability import correlation_middleware, setup_logging
from app.api.routes import api, public

DESCRIPTION = """
REST API of the Team 3 **Predictive Maintenance** solution, over the Databricks **Gold** layer.

* **Auth**: `X-API-Key` header (development) or `Authorization: Bearer <Entra ID token>` (deployed).
* **Tracing**: every response has an `X-Correlation-ID` header (send your own to trace a call).
* **Errors**: always `{"error": {"code", "message", "correlation_id", "details"}}`.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202 - runs once at startup
        app.state.db = GoldDB(settings)
        yield

    app = FastAPI(
        title="Team 3 Predictive Maintenance API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.middleware("http")(correlation_middleware)
    add_error_handlers(app)
    app.include_router(public)
    app.include_router(api)
    return app


app = create_app()
