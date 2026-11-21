"""Virtual file-system endpoints: folders, listing, tree, rename/move/delete."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.gateway.deps import (
    get_coordinator,
    get_current_user,
    get_db,
    get_settings,
    get_vfs,
)
from services.gateway.schemas import (
    CreateFolderRequest,
    DeleteResponse,
    NodeDetailResponse,
    NodeListResponse,
    NodeOut,
    PathResponse,
    TreeNodeOut,
    TreeResponse,
    UpdateNodeRequest,
    UsageOut,
)
from shared.config.settings import Settings
from shared.db.models import FILE, IndexStatus, User, VFSNode
from shared.errors import ConflictError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nodes"])


def _usage(db: Session, vfs, user_id: int) -> UsageOut:
    return UsageOut(**vfs.user_usage(db, user_id))


# ---------------------------------------------------------------------------
# Static routes must be declared before /nodes/{node_id}
# ---------------------------------------------------------------------------
@router.get("/nodes/root", response_model=NodeOut)
def get_root(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
) -> NodeOut:
    return NodeOut.model_validate(vfs.get_root(db, user.user_id))


@router.get("/nodes/tree", response_model=TreeResponse)
def get_tree(
    root_id: int | None = Query(default=None, ge=1),
    depth: int = Query(default=10, ge=0, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
) -> TreeResponse:
    tree = vfs.build_tree(db, user.user_id, root_id=root_id, depth=depth)
    return TreeResponse(root=TreeNodeOut.model_validate(tree))


@router.get("/nodes", response_model=NodeListResponse)
def list_nodes(
    parent_id: int | None = Query(default=None, ge=1),
    include_deleted: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=1000),
    sort_by: Literal["name", "updated_at", "created_at", "size", "type"] = "name",
    order: Literal["asc", "desc"] = "asc",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
    settings: Settings = Depends(get_settings),
) -> NodeListResponse:
    size = min(page_size or settings.default_page_size, settings.max_page_size)
    items, total, parent = vfs.list_children(
        db,
        user.user_id,
        parent_id,
        include_deleted=include_deleted,
        page=page,
        page_size=size,
        sort_by=sort_by,
        order=order,
    )
    return NodeListResponse(
        folder=NodeOut.model_validate(parent),
        items=[NodeOut.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=size,
        has_more=page * size < total,
        usage=_usage(db, vfs, user.user_id),
    )


@router.post("/folders", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
def create_folder(
    payload: CreateFolderRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
) -> NodeOut:
    node = vfs.create_folder(
        db,
        user.user_id,
        payload.name,
        payload.parent_id,
        description=payload.description,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("同一文件夹下已存在同名节点") from exc
    return NodeOut.model_validate(node)


@router.get("/nodes/{node_id}", response_model=NodeDetailResponse)
def get_node(
    node_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
) -> NodeDetailResponse:
    node = vfs.get_node(db, user.user_id, node_id)
    index_status = (
        db.get(IndexStatus, node.node_id) if node.node_type == FILE else None
    )
    return NodeDetailResponse(
        node=NodeOut.model_validate(node),
        index=index_status,
    )


@router.get("/nodes/{node_id}/path", response_model=PathResponse)
def get_path(
    node_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
) -> PathResponse:
    chain = vfs.get_path(db, user.user_id, node_id)
    return PathResponse(items=[NodeOut.model_validate(node) for node in chain])


@router.patch("/nodes/{node_id}", response_model=NodeDetailResponse)
def update_node(
    node_id: int,
    payload: UpdateNodeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
    coordinator=Depends(get_coordinator),
) -> NodeDetailResponse:
    node = vfs.get_node(db, user.user_id, node_id)
    old_name = node.name

    if payload.name is not None and payload.name.strip() != node.name:
        node = vfs.rename_node(db, user.user_id, node_id, payload.name)
    if (
        "parent_id" in payload.model_fields_set
        and payload.parent_id != node.parent_id
    ):
        node = vfs.move_node(db, user.user_id, node_id, payload.parent_id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("重命名或移动后与现有文件/文件夹重名") from exc

    index_status = None
    if node.node_type == FILE:
        if node.name != old_name:
            # Filename is part of both indexes.
            index_status = coordinator.index_file(db, node)
        else:
            index_status = db.get(IndexStatus, node.node_id)
    return NodeDetailResponse(
        node=NodeOut.model_validate(node), index=index_status
    )


@router.delete("/nodes/{node_id}", response_model=DeleteResponse)
def delete_node(
    node_id: int,
    recursive: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
    coordinator=Depends(get_coordinator),
) -> DeleteResponse:
    result = vfs.soft_delete(db, user.user_id, node_id, recursive=recursive)
    db.commit()
    vfs.finalize_delete(result)
    for file_id in result.file_ids:
        coordinator.delete_file_index(file_id)
    return DeleteResponse(
        deleted_node_ids=result.deleted_node_ids,
        deleted_file_ids=result.file_ids,
        freed_bytes=result.freed_bytes,
    )
