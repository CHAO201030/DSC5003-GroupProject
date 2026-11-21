"""Pytest fixtures for the CloudDisk test-suite.

The suite boots the real gRPC search and semantic servers in-process on
ephemeral ports and drives the real FastAPI application with TestClient.
Only MySQL is replaced by SQLite (the same SQLAlchemy models are used), and
the embedding backend is the deterministic offline hashing embedder, so no
model download is required.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.gateway.app import create_app  # noqa: E402
from services.search.engine import SearchEngine  # noqa: E402
from services.search.server import SearchGrpcServer  # noqa: E402
from services.semantic.embedder import build_embedder  # noqa: E402
from services.semantic.engine import SemanticEngine  # noqa: E402
from services.semantic.server import SemanticGrpcServer  # noqa: E402
from shared.config.settings import Settings  # noqa: E402


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    base = tmp_path_factory.mktemp("clouddisk-test")
    return Settings(
        database_url=f"sqlite:///{base / 'clouddisk.db'}",
        auto_create_tables=True,
        data_dir=base,
        object_store_dir=base / "objects",
        temp_dir=base / "tmp",
        log_dir=base / "logs",
        jwt_secret="test-secret-key-0123456789abcdef",
        bcrypt_rounds=4,
        access_token_expire_minutes=60,
        search_index_path=base / "search" / "search.db",
        semantic_index_path=base / "faiss" / "semantic.index",
        semantic_index_meta_path=base / "faiss" / "semantic_meta.json",
        semantic_model_cache_dir=base / "models",
        semantic_embedder="hash",
        semantic_allow_model_download=False,
        semantic_hash_dim=1024,
        enable_search_service=True,
        enable_semantic_service=True,
        search_port=0,
        semantic_port=0,
        search_timeout_seconds=5.0,
        index_timeout_seconds=15.0,
        health_timeout_seconds=1.0,
        frontend_dir=PROJECT_ROOT / "frontend",
    )


@pytest.fixture(scope="session")
def search_server(settings: Settings):
    engine = SearchEngine(settings.search_index_path)
    server = SearchGrpcServer(engine, "127.0.0.1", 0)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def semantic_server(settings: Settings):
    embedder = build_embedder(
        backend="hash",
        model_name="unused",
        cache_dir=settings.semantic_model_cache_dir,
        allow_download=False,
        hash_dim=settings.semantic_hash_dim,
    )
    engine = SemanticEngine(
        embedder,
        settings.semantic_index_path,
        settings.semantic_index_meta_path,
        chunk_size=settings.semantic_chunk_size,
        chunk_overlap=settings.semantic_chunk_overlap,
        max_chunks=settings.semantic_max_chunks,
    )
    server = SemanticGrpcServer(engine, "127.0.0.1", 0)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def app_settings(
    settings: Settings, search_server, semantic_server
) -> Settings:
    return replace(
        settings,
        search_port=search_server.bound_port,
        semantic_port=semantic_server.bound_port,
    )


@pytest.fixture(scope="session")
def app(app_settings: Settings):
    return create_app(app_settings)


@pytest.fixture(scope="session")
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def user(client: TestClient):
    from tests.utils import register_user

    return register_user(client)


@pytest.fixture()
def db_session(app):
    session = app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
