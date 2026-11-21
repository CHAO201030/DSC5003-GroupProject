"""Registration, login and current-user endpoints."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.gateway.deps import get_current_user, get_db, get_settings, get_vfs
from services.gateway.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from shared.auth import create_access_token, hash_password, validate_password, verify_password
from shared.config.settings import Settings
from shared.db.models import User
from shared.errors import ConflictError, ForbiddenError, UnauthorizedError, ValidationFailed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[\w.\-一-鿿]{3,32}$", re.UNICODE)


def _token_response(user: User, settings: Settings) -> TokenResponse:
    token = create_access_token(user.user_id, user.username, settings)
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    vfs=Depends(get_vfs),
) -> TokenResponse:
    username = payload.username.strip()
    if not _USERNAME_RE.match(username):
        raise ValidationFailed("用户名需为 3-32 位字母、数字、下划线、点、短横线或中文")
    validate_password(payload.password)

    existing = db.scalar(
        select(User.user_id).where(func.lower(User.username) == username.lower())
    )
    if existing is not None:
        raise ConflictError("用户名已被占用")

    user = User(
        username=username,
        password_hash=hash_password(payload.password, settings.bcrypt_rounds),
        status="ACTIVE",
    )
    db.add(user)
    db.flush()
    vfs.create_root(db, user.user_id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("用户名已被占用") from exc

    return _token_response(user, settings)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    username = payload.username.strip()
    user = db.scalar(
        select(User).where(func.lower(User.username) == username.lower()).limit(1)
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise UnauthorizedError("用户名或密码错误", code="INVALID_CREDENTIALS")
    if user.status != "ACTIVE":
        raise ForbiddenError("账号已被禁用", code="USER_DISABLED")
    return _token_response(user, settings)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
