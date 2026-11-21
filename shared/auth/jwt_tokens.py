"""JWT access-token creation and validation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from shared.config.settings import Settings
from shared.errors import UnauthorizedError


def create_access_token(
    user_id: int,
    username: str,
    settings: Settings,
    *,
    expires_minutes: int | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(
        minutes=expires_minutes
        if expires_minutes is not None
        else settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "username": username,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError(
            "登录已过期，请重新登录", code="TOKEN_EXPIRED"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("无效的访问令牌", code="TOKEN_INVALID") from exc

    if payload.get("type") != "access":
        raise UnauthorizedError("无效的访问令牌类型", code="TOKEN_INVALID")
    try:
        int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("无效的访问令牌", code="TOKEN_INVALID") from exc
    return payload
