"""FastAPI application. Run: uvicorn app.api.main:app --reload  ->  Swagger UI at /docs"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app.api.db import GoldDB
from app.api.errors import add_error_handlers
from app.api.observability import correlation_middleware
from app.api.routes import api, auth, public
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging

DESCRIPTION = """
REST API of the Team 3 **Predictive Maintenance** solution, over the Databricks **Gold** layer.

* **Auth**: session cookie from `POST /auth/login` (website, HttpOnly + Secure), `X-API-Key` header
  (development) or `Authorization: Bearer <Entra ID token>` (deployed).
* **Tracing**: every response has an `X-Correlation-ID` header (send your own to trace a call).
* **Errors**: always `{"error": {"code", "message", "correlation_id", "details"}}`.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.check_production()  # refuses to start with unsafe auth in prod
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202 - runs once at startup
        app.state.db = GoldDB(settings)
        from app.agent.tools.graph_sync import start_background_sync

        start_background_sync()  # copy the Gold knowledge graph into Neo4j if NEO4J_URI is set (thread, never blocks)
        yield

    app = FastAPI(
        title="Team 3 Predictive Maintenance API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan
    )
    app.state.dashboard_cache = {}  # per app, so tests never share cached payloads
    app.state.login_failures = {}
    app.dependency_overrides[get_settings] = lambda: settings
    app.middleware("http")(correlation_middleware)
    add_error_handlers(app)
    app.include_router(public)
    app.include_router(auth)
    app.include_router(api)
    if settings.frontend_dist:
        _serve_frontend(app, settings.frontend_dist)
    return app


class SPAFiles(StaticFiles):
    """Static files that fall back to index.html, so React Router deep links survive a page refresh."""

    async def get_response(self, path: str, scope):  # noqa: ANN001, ANN201
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or scope.get("is_api"):  # an unknown /api/... stays a JSON 404
                raise
            return await super().get_response("index.html", scope)


def _serve_frontend(app: FastAPI, dist: str) -> None:
    """Same origin for site and API (the session cookie is SameSite=Strict). The browser calls /api/...;
    the prefix is removed here, exactly as the Vite dev proxy does."""

    @app.middleware("http")
    async def strip_api_prefix(request: Request, call_next):  # noqa: ANN202
        if request.scope["path"].startswith("/api/"):
            request.scope["path"] = request.scope["path"][4:]
            request.scope["is_api"] = True
        return await call_next(request)

    app.mount("/", SPAFiles(directory=dist, html=True), name="frontend")  # last: routers match first


app = create_app()
