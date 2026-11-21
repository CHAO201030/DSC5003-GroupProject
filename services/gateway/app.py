"""FastAPI application factory.

The Gateway and the VFS/Storage service are deployed as one process in v1
(explicitly allowed by the requirements) but remain separate modules with a
clear service boundary: routers only talk to ``VFSStorageService``, never to
files or storage tables directly.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.gateway.coordinator import IndexCoordinator
from services.gateway.deps import (
    get_db,
    get_search_client,
    get_semantic_client,
    get_settings,
)
from services.gateway.errors import register_exception_handlers
from services.gateway.routers import auth, files, index, nodes, search
from services.gateway.schemas import HealthResponse, ServiceHealthOut
from services.search.client import SearchClient
from services.semantic.client import SemanticClient
from services.vfs_storage import VFSStorageService
from shared import __version__
from shared.config.settings import Settings, get_settings as _get_settings
from shared.db.session import init_db, make_engine, make_session_factory
from shared.errors import NotFoundError

logger = logging.getLogger(__name__)


def _health_out(health) -> ServiceHealthOut:
    return ServiceHealthOut(**asdict(health))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or _get_settings()
    settings.ensure_directories()

    engine = make_engine(settings.database_url, echo=settings.database_echo)
    if settings.auto_create_tables:
        init_db(engine)
    session_factory = make_session_factory(engine)

    vfs = VFSStorageService(settings)
    search_client = SearchClient(
        settings.search_host,
        settings.search_port,
        timeout=settings.search_timeout_seconds,
        index_timeout=settings.index_timeout_seconds,
        enabled=settings.enable_search_service,
    )
    semantic_client = SemanticClient(
        settings.semantic_host,
        settings.semantic_port,
        timeout=settings.search_timeout_seconds,
        index_timeout=settings.index_timeout_seconds,
        enabled=settings.enable_semantic_service,
    )
    coordinator = IndexCoordinator(settings, vfs, search_client, semantic_client)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.using_default_jwt_secret:
            logger.warning(
                "JWT_SECRET is using an insecure default; set a long random secret "
                "in .env before exposing the service."
            )
        logger.info("CloudDisk gateway started (database=%s)", _safe_db_name(settings.database_url))
        try:
            yield
        finally:
            search_client.close()
            semantic_client.close()
            engine.dispose()
            logger.info("CloudDisk gateway stopped")

    app = FastAPI(
        title="CloudDisk API",
        description=(
            "CloudDisk 云盘文档管理系统：VFS + MD5 内容寻址存储 + 关键词搜索 + 语义搜索。"
        ),
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.vfs = vfs
    app.state.search_client = search_client
    app.state.semantic_client = semantic_client
    app.state.coordinator = coordinator

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "X-Content-MD5", "X-Content-Size"],
    )
    register_exception_handlers(app)

    api = APIRouter(prefix="/api")
    api.include_router(auth.router)
    api.include_router(nodes.router)
    api.include_router(files.router)
    api.include_router(search.router)
    api.include_router(index.router)
    app.include_router(api)

    @app.get("/api/health", response_model=HealthResponse, tags=["health"])
    @app.get("/health", response_model=HealthResponse, include_in_schema=False)
    def health(
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
        search_client=Depends(get_search_client),
        semantic_client=Depends(get_semantic_client),
    ) -> HealthResponse:
        database = "ok"
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            db.rollback()
            database = "error"

        search_health = search_client.health(settings.health_timeout_seconds)
        semantic_health = semantic_client.health(settings.health_timeout_seconds)
        overall = (
            "ok"
            if database == "ok"
            and (not settings.enable_search_service or search_health.healthy)
            and (not settings.enable_semantic_service or semantic_health.healthy)
            else "degraded"
        )
        return HealthResponse(
            status=overall,
            app=settings.app_name,
            version=__version__,
            database=database,
            services={
                "search": _health_out(search_health),
                "semantic": _health_out(semantic_health),
            },
        )

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def api_not_found(rest: str):
        raise NotFoundError(f"接口不存在: /api/{rest}", code="API_NOT_FOUND")

    frontend_dir = settings.frontend_dir
    if frontend_dir.is_dir() and (frontend_dir / "index.html").is_file():
        app.mount(
            "/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend"
        )
    else:  # pragma: no cover - helpful fallback for API-only deployments
        @app.get("/", include_in_schema=False)
        def root():
            return {
                "app": settings.app_name,
                "version": __version__,
                "docs": "/api/docs",
                "frontend": "not installed",
            }

    return app


def _safe_db_name(database_url: str) -> str:
    """Hide credentials when logging the database URL."""
    if "://" not in database_url:
        return database_url
    scheme, rest = database_url.split("://", 1)
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    return f"{scheme}://{rest.split('?', 1)[0]}"
