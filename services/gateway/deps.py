"""FastAPI dependencies shared by the gateway routers."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from shared.auth import decode_access_token
from shared.config.settings import Settings
from shared.db.models import User
from shared.errors import UnauthorizedError

_bearer = HTTPBearer(auto_error=False, description="JWT access token")


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_vfs(request: Request):
    return request.app.state.vfs


def get_coordinator(request: Request):
    return request.app.state.coordinator


def get_search_client(request: Request):
    return request.app.state.search_client


def get_semantic_client(request: Request):
    return request.app.state.semantic_client


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or (credentials.scheme or "").lower() != "bearer":
        raise UnauthorizedError("请先登录", code="TOKEN_MISSING")

    settings: Settings = request.app.state.settings
    payload = decode_access_token(credentials.credentials, settings)
    user = db.get(User, int(payload["sub"]))
    if user is None or user.status != "ACTIVE":
        raise UnauthorizedError("用户不存在或已被禁用", code="USER_INACTIVE")
    return user
