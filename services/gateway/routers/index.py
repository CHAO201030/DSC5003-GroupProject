"""Index status, retry and rebuild endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from services.gateway.coordinator import run_rebuild_background
from services.gateway.deps import (
    get_coordinator,
    get_current_user,
    get_db,
    get_search_client,
    get_semantic_client,
    get_settings,
)
from services.gateway.schemas import (
    FileIndexItem,
    FileIndexListResponse,
    IndexSummaryOut,
    NodeOut,
    RebuildRequest,
    RebuildResponse,
    RetryResponse,
    ServiceHealthOut,
)
from shared.config.settings import Settings
from shared.db.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/index", tags=["index"])


def _health_out(health) -> ServiceHealthOut:
    return ServiceHealthOut(**asdict(health))


@router.get("/status", response_model=IndexSummaryOut)
def index_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    coordinator=Depends(get_coordinator),
    search_client=Depends(get_search_client),
    semantic_client=Depends(get_semantic_client),
) -> IndexSummaryOut:
    counts = coordinator.summary(db, user.user_id)
    search_health = search_client.health(settings.health_timeout_seconds)
    semantic_health = semantic_client.health(settings.health_timeout_seconds)
    return IndexSummaryOut(
        **counts,
        search_enabled=settings.enable_search_service,
        semantic_enabled=settings.enable_semantic_service,
        search_service=_health_out(search_health),
        semantic_service=_health_out(semantic_health),
    )


@router.get("/files", response_model=FileIndexListResponse)
def file_statuses(
    status_filter: Literal["", "all", "failed", "pending", "ready"] = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    coordinator=Depends(get_coordinator),
) -> FileIndexListResponse:
    size = min(page_size or settings.default_page_size, settings.max_page_size)
    rows, total = coordinator.list_file_statuses(
        db,
        user.user_id,
        status_filter="" if status_filter == "all" else status_filter,
        page=page,
        page_size=size,
    )
    items = [
        FileIndexItem(
            file_id=node.node_id,
            name=node.name,
            file_size=node.file_size,
            mime_type=node.mime_type,
            version=node.version,
            updated_at=node.updated_at,
            index=status,
        )
        for node, status in rows
    ]
    return FileIndexListResponse(
        items=items, total=total, page=page, page_size=size
    )


@router.post("/{file_id}/retry", response_model=RetryResponse)
def retry_file_index(
    file_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    coordinator=Depends(get_coordinator),
) -> RetryResponse:
    node, status = coordinator.retry_file(db, user.user_id, file_id)
    return RetryResponse(
        node=NodeOut.model_validate(node), index=status
    )


@router.post("/rebuild", response_model=RebuildResponse)
def rebuild_indexes(
    payload: RebuildRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    coordinator=Depends(get_coordinator),
) -> RebuildResponse:
    if payload.background:
        background_tasks.add_task(
            run_rebuild_background,
            request.app.state.session_factory,
            coordinator,
            user.user_id,
            payload.kinds,
        )
        return RebuildResponse(status="scheduled")
    result = coordinator.rebuild(db, user.user_id, payload.kinds)
    return RebuildResponse(**result)
