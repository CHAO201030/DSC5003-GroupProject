"""File upload, content replacement, download and metadata."""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import FileResponse
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
    NodeDetailResponse,
    NodeOut,
    ReplaceContentResponse,
    UploadResponse,
)
from shared.config.settings import Settings
from shared.db.models import FILE, IndexStatus, User
from shared.errors import (
    ConflictError,
    PayloadTooLargeError,
    StorageUnavailableError,
    ValidationFailed,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["files"])

_CHUNK_SIZE = 1024 * 1024


def _stream_upload(upload: UploadFile, settings: Settings) -> tuple[Path, str, int]:
    """Write the upload to a temporary file while hashing it (streaming)."""
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=settings.temp_dir, prefix="upload-", suffix=".part"
    )
    path = Path(handle.name)
    digest = hashlib.md5()
    size = 0
    try:
        while True:
            chunk = upload.file.read(_CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise PayloadTooLargeError(
                    f"文件超过 {settings.max_upload_bytes} 字节限制"
                )
            digest.update(chunk)
            handle.write(chunk)
        handle.flush()
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    handle.close()
    return path, digest.hexdigest(), size


@router.post(
    "/files/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED
)
def upload_file(
    file: UploadFile = File(...),
    parent_id: int | None = Form(default=None),
    description: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
    settings: Settings = Depends(get_settings),
    coordinator=Depends(get_coordinator),
) -> UploadResponse:
    temp_path: Path | None = None
    try:
        temp_path, md5, size = _stream_upload(file, settings)
        try:
            result = vfs.create_file_from_upload(
                db,
                user.user_id,
                temp_path=temp_path,
                filename=file.filename or "未命名文件",
                md5=md5,
                size=size,
                parent_id=parent_id,
                mime_type=file.content_type,
                description=(description or "").strip() or None,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError("同名文件已存在或数据冲突") from exc
        except Exception:
            db.rollback()
            raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        try:
            file.file.close()
        except Exception:  # pragma: no cover
            pass

    index_status = coordinator.index_file(db, result.node)
    return UploadResponse(
        node=NodeOut.model_validate(result.node),
        deduplicated=result.deduplicated,
        created_physical=result.created_physical,
        index=index_status,
    )


@router.put("/files/{file_id}/content", response_model=ReplaceContentResponse)
def replace_content(
    file_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
    settings: Settings = Depends(get_settings),
    coordinator=Depends(get_coordinator),
) -> ReplaceContentResponse:
    vfs.get_file_node(db, user.user_id, file_id)  # fail fast if not accessible
    temp_path: Path | None = None
    try:
        temp_path, md5, size = _stream_upload(file, settings)
        try:
            result = vfs.replace_file_content(
                db,
                user.user_id,
                file_id,
                temp_path=temp_path,
                md5=md5,
                size=size,
                mime_type=file.content_type,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError("内容更新发生数据冲突") from exc
        except Exception:
            db.rollback()
            raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        try:
            file.file.close()
        except Exception:  # pragma: no cover
            pass

    if result.old_object_path is not None:
        vfs.storage.delete_path(result.old_object_path)
    index_status = coordinator.index_file(db, result.node)
    return ReplaceContentResponse(
        node=NodeOut.model_validate(result.node),
        deduplicated=result.deduplicated,
        index=index_status,
    )


@router.get("/files/{file_id}/download")
def download_file(
    file_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
) -> FileResponse:
    try:
        path, node = vfs.get_download_info(db, user.user_id, file_id)
    except Exception:
        db.rollback()
        raise
    if not path.is_file():
        raise StorageUnavailableError("文件实体缺失")
    headers = {
        "X-Content-MD5": node.content_md5 or "",
        "Cache-Control": "private, no-store",
        "X-Content-Size": str(node.file_size),
    }
    return FileResponse(
        str(path),
        media_type=node.mime_type or "application/octet-stream",
        filename=node.name,
        headers=headers,
    )


@router.get("/files/{file_id}", response_model=NodeDetailResponse)
def file_metadata(
    file_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
) -> NodeDetailResponse:
    node = vfs.get_file_node(db, user.user_id, file_id)
    index_status = db.get(IndexStatus, node.node_id)
    return NodeDetailResponse(
        node=NodeOut.model_validate(node), index=index_status
    )
