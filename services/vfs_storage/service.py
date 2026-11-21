"""Virtual file system + MD5 content-addressed storage service.

The service is deliberately framework-free: the Gateway calls it in-process
(the requirements explicitly allow merging gateway and vfs-storage in v1),
while every method keeps a clear service boundary and never performs HTTP or
gRPC work.  Callers own the transaction (``db.commit()``) so that physical
cleanup can happen only after a successful commit.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import socket
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.config.settings import Settings
from shared.db.models import (
    ACTIVE,
    ACTIVE_DELETE_TOKEN,
    DELETED,
    FILE,
    FOLDER,
    IndexStatus,
    ObjectLocation,
    StorageNode,
    StorageObject,
    VFSNode,
    utcnow,
)
from shared.errors import (
    ConflictError,
    NotFoundError,
    StorageIntegrityError,
    ValidationFailed,
)

from .storage import MD5_PATTERN, ObjectStorage
from .text_extract import ExtractionResult, extract_text

logger = logging.getLogger(__name__)

_INVALID_NAME_CHARS = {"/", "\\", "\x00"}
_CONTROL_CHARS = re.compile(r"[\x01-\x1f\x7f]")
_FORBIDDEN_NAMES = {".", ".."}


@dataclass(slots=True)
class UploadResult:
    node: VFSNode
    storage_object: StorageObject
    deduplicated: bool
    created_physical: bool


@dataclass(slots=True)
class ReplaceResult:
    node: VFSNode
    storage_object: StorageObject
    deduplicated: bool
    old_object_path: Path | None = None
    old_object_id: int | None = None
    freed_bytes: int = 0


@dataclass(slots=True)
class DeleteResult:
    deleted_node_ids: list[int] = field(default_factory=list)
    file_ids: list[int] = field(default_factory=list)
    object_paths: list[Path] = field(default_factory=list)
    freed_object_ids: list[int] = field(default_factory=list)
    freed_bytes: int = 0


class VFSStorageService:
    ROOT_FOLDER_NAME = "我的文件"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = ObjectStorage(settings)
        self._upload_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Nodes and lookup
    # ------------------------------------------------------------------
    def get_node(
        self,
        db: Session,
        user_id: int,
        node_id: int,
        *,
        include_deleted: bool = False,
    ) -> VFSNode:
        node = db.get(VFSNode, node_id)
        if node is None or node.owner_id != user_id:
            raise NotFoundError("节点不存在")
        if not include_deleted and node.status != ACTIVE:
            raise NotFoundError("节点不存在")
        return node

    def get_file_node(self, db: Session, user_id: int, node_id: int) -> VFSNode:
        node = self.get_node(db, user_id, node_id)
        if node.node_type != FILE:
            raise ValidationFailed("该节点不是文件")
        return node

    def resolve_parent(self, db: Session, user_id: int, parent_id: int | None) -> VFSNode:
        if parent_id is None:
            return self.get_root(db, user_id)
        parent = db.get(VFSNode, parent_id)
        if parent is None or parent.owner_id != user_id or parent.status != ACTIVE:
            raise NotFoundError("目标文件夹不存在")
        if parent.node_type != FOLDER:
            raise ValidationFailed("目标节点不是文件夹")
        return parent

    def create_root(self, db: Session, user_id: int) -> VFSNode:
        node = VFSNode(
            owner_id=user_id,
            parent_id=None,
            node_type=FOLDER,
            name=self.ROOT_FOLDER_NAME,
            file_size=0,
            version=1,
            status=ACTIVE,
            delete_token=ACTIVE_DELETE_TOKEN,
        )
        db.add(node)
        db.flush()
        return node

    def get_root(self, db: Session, user_id: int) -> VFSNode:
        node = db.scalar(
            select(VFSNode)
            .where(
                VFSNode.owner_id == user_id,
                VFSNode.parent_id.is_(None),
                VFSNode.node_type == FOLDER,
                VFSNode.status == ACTIVE,
            )
            .order_by(VFSNode.node_id)
            .limit(1)
        )
        if node is None:
            node = self.create_root(db, user_id)
        return node

    def get_path(self, db: Session, user_id: int, node_id: int) -> list[VFSNode]:
        current = self.get_node(db, user_id, node_id, include_deleted=True)
        chain: list[VFSNode] = []
        seen: set[int] = set()
        while current is not None:
            if current.node_id in seen or len(chain) > 512:
                raise StorageIntegrityError("检测到循环的目录结构")
            seen.add(current.node_id)
            chain.append(current)
            current = db.get(VFSNode, current.parent_id) if current.parent_id else None
            if current is not None and current.owner_id != user_id:
                raise StorageIntegrityError("目录结构跨越了用户边界")
        chain.reverse()
        return chain

    # ------------------------------------------------------------------
    # Listing / tree
    # ------------------------------------------------------------------
    def list_children(
        self,
        db: Session,
        user_id: int,
        parent_id: int | None,
        *,
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "name",
        order: str = "asc",
    ) -> tuple[list[VFSNode], int, VFSNode]:
        parent = self.resolve_parent(db, user_id, parent_id)

        conditions = [VFSNode.owner_id == user_id, VFSNode.parent_id == parent.node_id]
        if not include_deleted:
            conditions.append(VFSNode.status == ACTIVE)

        sort_columns = {
            "name": func.lower(VFSNode.name),
            "updated_at": VFSNode.updated_at,
            "created_at": VFSNode.created_at,
            "size": VFSNode.file_size,
            "type": VFSNode.node_type,
        }
        sort_column = sort_columns.get(sort_by, sort_columns["name"])
        direction = sort_column.desc() if order.lower() == "desc" else sort_column.asc()

        base = select(VFSNode).where(*conditions)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        stmt = (
            base.order_by(
                (VFSNode.node_type == FOLDER).desc(),  # folders first
                direction,
                VFSNode.node_id.asc(),
            )
            .offset(max(page - 1, 0) * page_size)
            .limit(page_size)
        )
        items = list(db.scalars(stmt).all())
        return items, int(total), parent

    def build_tree(
        self, db: Session, user_id: int, root_id: int | None = None, depth: int = 10
    ) -> dict:
        root = self.get_node(db, user_id, root_id) if root_id else self.get_root(db, user_id)
        if root.node_type != FOLDER:
            raise ValidationFailed("目录树根节点必须是文件夹")

        nodes = list(
            db.scalars(
                select(VFSNode).where(
                    VFSNode.owner_id == user_id, VFSNode.status == ACTIVE
                )
            ).all()
        )
        children: dict[int | None, list[VFSNode]] = {}
        for node in nodes:
            children.setdefault(node.parent_id, []).append(node)
        for bucket in children.values():
            bucket.sort(key=lambda n: (n.node_type != FOLDER, n.name.lower(), n.node_id))

        def serialise(node: VFSNode, remaining: int) -> dict:
            payload = {
                "id": node.node_id,
                "parent_id": node.parent_id,
                "name": node.name,
                "node_type": node.node_type,
                "file_size": node.file_size,
                "mime_type": node.mime_type,
                "version": node.version,
                "updated_at": node.updated_at,
                "children": [],
            }
            if remaining > 0:
                payload["children"] = [
                    serialise(child, remaining - 1)
                    for child in children.get(node.node_id, [])
                ]
            return payload

        return serialise(root, max(depth, 0))

    def user_usage(self, db: Session, user_id: int) -> dict:
        total_size, file_count = db.execute(
            select(
                func.coalesce(func.sum(VFSNode.file_size), 0),
                func.count(VFSNode.node_id),
            ).where(
                VFSNode.owner_id == user_id,
                VFSNode.status == ACTIVE,
                VFSNode.node_type == FILE,
            )
        ).one()
        folder_count = db.scalar(
            select(func.count(VFSNode.node_id)).where(
                VFSNode.owner_id == user_id,
                VFSNode.status == ACTIVE,
                VFSNode.node_type == FOLDER,
            )
        ) or 0
        return {
            "file_count": int(file_count or 0),
            "folder_count": int(folder_count or 0),
            "total_bytes": int(total_size or 0),
        }

    # ------------------------------------------------------------------
    # Folder / rename / move
    # ------------------------------------------------------------------
    def create_folder(
        self,
        db: Session,
        user_id: int,
        name: str,
        parent_id: int | None,
        description: str | None = None,
    ) -> VFSNode:
        name = self.validate_name(name, FILE)
        parent = self.resolve_parent(db, user_id, parent_id)
        self._assert_name_free(db, user_id, parent.node_id, name)
        node = VFSNode(
            owner_id=user_id,
            parent_id=parent.node_id,
            node_type=FOLDER,
            name=name,
            file_size=0,
            version=1,
            status=ACTIVE,
            delete_token=ACTIVE_DELETE_TOKEN,
            description=(description or None),
        )
        db.add(node)
        db.flush()
        return node

    def rename_node(self, db: Session, user_id: int, node_id: int, new_name: str) -> VFSNode:
        node = self.get_node(db, user_id, node_id)
        if node.parent_id is None:
            raise ValidationFailed("不能重命名根目录")
        new_name = self.validate_name(new_name, node.node_type)
        if node.name == new_name:
            return node
        self._assert_name_free(
            db, user_id, node.parent_id, new_name, exclude_node_id=node.node_id
        )
        node.name = new_name
        node.updated_at = utcnow()
        db.flush()
        return node

    def move_node(
        self, db: Session, user_id: int, node_id: int, new_parent_id: int | None
    ) -> VFSNode:
        node = self.get_node(db, user_id, node_id)
        if node.parent_id is None:
            raise ValidationFailed("不能移动根目录")
        target = self.resolve_parent(db, user_id, new_parent_id)
        if target.node_id == node.parent_id:
            return node
        if target.node_id == node.node_id:
            raise ValidationFailed("不能把节点移动到自身")

        ancestor = target
        guard = 0
        while ancestor is not None:
            guard += 1
            if guard > 512:
                raise StorageIntegrityError("目录层级异常")
            if ancestor.node_id == node.node_id:
                raise ValidationFailed("不能把文件夹移动到它自己的子文件夹中")
            ancestor = db.get(VFSNode, ancestor.parent_id) if ancestor.parent_id else None

        self._assert_name_free(
            db, user_id, target.node_id, node.name, exclude_node_id=node.node_id
        )
        node.parent_id = target.node_id
        node.updated_at = utcnow()
        db.flush()
        return node

    # ------------------------------------------------------------------
    # Upload / content update
    # ------------------------------------------------------------------
    def create_file_from_upload(
        self,
        db: Session,
        user_id: int,
        *,
        temp_path: str | Path,
        filename: str,
        md5: str,
        size: int,
        parent_id: int | None = None,
        mime_type: str | None = None,
        description: str | None = None,
    ) -> UploadResult:
        if not MD5_PATTERN.match(md5 or ""):
            raise ValidationFailed("文件 MD5 格式不合法")
        if size < 0:
            raise ValidationFailed("文件大小不能为负")
        name = self.normalise_upload_name(filename)
        parent = self.resolve_parent(db, user_id, parent_id)

        with self._upload_lock:
            self._assert_name_free(db, user_id, parent.node_id, name)
            storage_object, deduplicated, created_physical = self._link_storage_object(
                db, md5, size, temp_path
            )
            node = VFSNode(
                owner_id=user_id,
                parent_id=parent.node_id,
                node_type=FILE,
                name=name,
                storage_object_id=storage_object.object_id,
                file_size=size,
                mime_type=self._guess_mime(name, mime_type),
                content_md5=md5,
                version=1,
                status=ACTIVE,
                delete_token=ACTIVE_DELETE_TOKEN,
                description=(description or None),
            )
            db.add(node)
            db.flush()
            db.add(
                IndexStatus(
                    file_id=node.node_id,
                    current_version=1,
                    keyword_status="PENDING",
                    semantic_status="PENDING",
                    retry_count=0,
                )
            )
            db.flush()
            return UploadResult(
                node=node,
                storage_object=storage_object,
                deduplicated=deduplicated,
                created_physical=created_physical,
            )

    def replace_file_content(
        self,
        db: Session,
        user_id: int,
        node_id: int,
        *,
        temp_path: str | Path,
        md5: str,
        size: int,
        mime_type: str | None = None,
    ) -> ReplaceResult:
        if not MD5_PATTERN.match(md5 or ""):
            raise ValidationFailed("文件 MD5 格式不合法")
        node = self.get_file_node(db, user_id, node_id)

        if node.content_md5 == md5 and node.file_size == size:
            self.storage.discard_temp(temp_path)
            assert node.storage_object is not None
            return ReplaceResult(
                node=node, storage_object=node.storage_object, deduplicated=True
            )

        with self._upload_lock:
            old_object = node.storage_object
            old_object_id = old_object.object_id if old_object else None
            old_md5 = node.content_md5
            old_size = old_object.file_size if old_object else 0

            new_object, deduplicated, _ = self._link_storage_object(
                db, md5, size, temp_path
            )
            node.storage_object_id = new_object.object_id
            node.content_md5 = md5
            node.file_size = size
            node.mime_type = self._guess_mime(node.name, mime_type or node.mime_type)
            node.version = (node.version or 1) + 1
            node.updated_at = utcnow()

            old_path: Path | None = None
            freed_bytes = 0
            if old_object is not None and old_object.object_id != new_object.object_id:
                old_object.ref_count = max(0, old_object.ref_count - 1)
                if old_object.ref_count == 0:
                    old_path = self.storage.path_for(old_md5) if old_md5 else None
                    freed_bytes = old_size
                    # The storage_objects row is retained as content metadata
                    # (soft-deleted VFS nodes still reference it); only the
                    # physical file and its location records are removed.
                    db.execute(
                        sa_delete(ObjectLocation).where(
                            ObjectLocation.object_id == old_object.object_id
                        )
                    )

            status = db.get(IndexStatus, node.node_id)
            if status is None:
                status = IndexStatus(file_id=node.node_id)
                db.add(status)
            status.current_version = node.version
            status.keyword_status = "PENDING"
            status.semantic_status = "PENDING"
            status.keyword_index_version = 0
            status.semantic_model_version = None
            status.retry_count = 0
            status.last_error = None
            status.updated_at = utcnow()
            db.flush()

            return ReplaceResult(
                node=node,
                storage_object=new_object,
                deduplicated=deduplicated,
                old_object_path=old_path,
                old_object_id=old_object_id,
                freed_bytes=freed_bytes,
            )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def soft_delete(
        self,
        db: Session,
        user_id: int,
        node_id: int,
        *,
        recursive: bool = True,
    ) -> DeleteResult:
        node = self.get_node(db, user_id, node_id)
        if node.parent_id is None:
            raise ValidationFailed("不能删除根目录")

        if node.node_type == FOLDER:
            has_child = db.scalar(
                select(VFSNode.node_id)
                .where(VFSNode.parent_id == node.node_id, VFSNode.status == ACTIVE)
                .limit(1)
            )
            if has_child and not recursive:
                raise ConflictError("文件夹非空，请使用递归删除")

        nodes = [node]
        if node.node_type == FOLDER:
            nodes.extend(self._collect_descendants(db, user_id, node.node_id))

        result = DeleteResult()
        deleted_at = utcnow()
        token = f"del-{uuid.uuid4().hex}"
        object_cache: dict[int, StorageObject] = {}

        for item in nodes:
            if item.node_type == FILE and item.storage_object_id:
                file_id = item.node_id
                result.file_ids.append(file_id)
                obj = object_cache.get(item.storage_object_id)
                if obj is None:
                    obj = db.get(StorageObject, item.storage_object_id)
                    if obj is not None:
                        object_cache[obj.object_id] = obj
                if obj is not None:
                    obj.ref_count = max(0, obj.ref_count - 1)
                    if obj.ref_count == 0 and obj.object_id not in result.freed_object_ids:
                        result.freed_object_ids.append(obj.object_id)
                        md5 = obj.content_md5
                        result.object_paths.append(self.storage.path_for(md5))
                        result.freed_bytes += obj.file_size
                        # Keep the storage_objects row (content metadata) but
                        # remove the physical location; the file is deleted
                        # after the transaction commits.
                        db.execute(
                            sa_delete(ObjectLocation).where(
                                ObjectLocation.object_id == obj.object_id
                            )
                        )
                db.execute(sa_delete(IndexStatus).where(IndexStatus.file_id == file_id))
            item.status = DELETED
            item.deleted_at = deleted_at
            item.delete_token = f"{token}-{item.node_id}"[:64]
            item.updated_at = deleted_at

        db.flush()
        result.deleted_node_ids = [item.node_id for item in nodes]
        return result

    def finalize_delete(self, result: DeleteResult) -> None:
        """Remove physical files *after* the database transaction commits."""
        for path in dict.fromkeys(result.object_paths):
            self.storage.delete_path(path)

    # ------------------------------------------------------------------
    # Download / indexing helpers
    # ------------------------------------------------------------------
    def get_download_info(
        self, db: Session, user_id: int, node_id: int
    ) -> tuple[Path, VFSNode]:
        node = self.get_file_node(db, user_id, node_id)
        if not node.content_md5:
            raise StorageIntegrityError("文件缺少内容 MD5")
        try:
            path = self.storage.absolute_path(node.content_md5)
        except StorageIntegrityError as exc:
            raise StorageIntegrityError("文件实体缺失，请联系管理员重建存储") from exc
        return path, node

    def extract_for_index(
        self, db: Session, node: VFSNode
    ) -> ExtractionResult:
        if node.node_type != FILE or not node.content_md5:
            return ExtractionResult("", False, "not-a-file")
        try:
            path = self.storage.path_for(node.content_md5)
        except StorageIntegrityError:
            return ExtractionResult("", False, "invalid-md5")
        if not path.is_file():
            logger.warning("Physical object missing for node %s (%s)", node.node_id, node.content_md5)
            return ExtractionResult("", False, "missing")
        return extract_text(
            path,
            mime_type=node.mime_type,
            filename=node.name,
            max_bytes=self.settings.max_index_bytes,
            max_chars=self.settings.max_index_chars,
        )

    def active_files_by_ids(
        self, db: Session, user_id: int, file_ids: list[int | str]
    ) -> dict[int, VFSNode]:
        normalised: list[int] = []
        for value in file_ids:
            try:
                normalised.append(int(value))
            except (TypeError, ValueError):
                continue
        if not normalised:
            return {}
        rows = db.scalars(
            select(VFSNode).where(
                VFSNode.node_id.in_(normalised),
                VFSNode.owner_id == user_id,
                VFSNode.status == ACTIVE,
                VFSNode.node_type == FILE,
            )
        ).all()
        return {node.node_id: node for node in rows}

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def validate_name(name: str, node_type: str = FILE) -> str:
        name = (name or "").strip()
        if not name:
            raise ValidationFailed("名称不能为空")
        if len(name) > 255:
            raise ValidationFailed("名称不能超过 255 个字符")
        if any(char in name for char in _INVALID_NAME_CHARS):
            raise ValidationFailed("名称不能包含 / 或 \\")
        if _CONTROL_CHARS.search(name):
            raise ValidationFailed("名称不能包含控制字符")
        if name in _FORBIDDEN_NAMES:
            raise ValidationFailed("名称不合法")
        return name

    def normalise_upload_name(self, filename: str) -> str:
        # Browsers may send C:\fakepath\name.txt; keep only the leaf name.
        leaf = (filename or "").replace("\\", "/").split("/")[-1].strip()
        if not leaf:
            leaf = "未命名文件"
        return self.validate_name(leaf, FILE)

    def _assert_name_free(
        self,
        db: Session,
        owner_id: int,
        parent_id: int | None,
        name: str,
        *,
        exclude_node_id: int | None = None,
    ) -> None:
        stmt = select(VFSNode.node_id).where(
            VFSNode.owner_id == owner_id,
            VFSNode.parent_id == parent_id,
            VFSNode.name == name,
            VFSNode.delete_token == ACTIVE_DELETE_TOKEN,
            VFSNode.status == ACTIVE,
        )
        if exclude_node_id is not None:
            stmt = stmt.where(VFSNode.node_id != exclude_node_id)
        if db.scalar(stmt.limit(1)) is not None:
            raise ConflictError(f"同一文件夹下已存在名为“{name}”的文件或文件夹")

    def _collect_descendants(self, db: Session, user_id: int, root_id: int) -> list[VFSNode]:
        collected: list[VFSNode] = []
        frontier = [root_id]
        guard = 0
        while frontier:
            guard += 1
            if guard > 10_000:
                raise StorageIntegrityError("目录层级过深")
            children = list(
                db.scalars(
                    select(VFSNode).where(
                        VFSNode.parent_id.in_(frontier),
                        VFSNode.owner_id == user_id,
                        VFSNode.status == ACTIVE,
                    )
                ).all()
            )
            if not children:
                break
            collected.extend(children)
            frontier = [child.node_id for child in children if child.node_type == FOLDER]
        return collected

    def _link_storage_object(
        self, db: Session, md5: str, size: int, temp_path: str | Path
    ) -> tuple[StorageObject, bool, bool]:
        """Find-or-create the storage object and increment its ref count.

        Returns ``(object, deduplicated, created_physical_file)``.
        """
        obj = db.scalar(
            select(StorageObject).where(StorageObject.content_md5 == md5).limit(1)
        )
        if obj is not None:
            if obj.file_size != size:
                raise StorageIntegrityError(
                    "相同 MD5 的存储对象大小不一致，可能存在数据损坏"
                )
            if self.storage.exists(md5):
                self.storage.discard_temp(temp_path)
                created_physical = False
            else:  # self-heal a missing physical file using the new upload
                created_physical = self.storage.persist_temp(temp_path, md5, size)
            obj.ref_count = (obj.ref_count or 0) + 1
            self._ensure_object_location(db, obj)
            db.flush()
            return obj, True, created_physical

        created_physical = self.storage.persist_temp(temp_path, md5, size)
        obj = StorageObject(
            content_md5=md5,
            file_size=size,
            storage_key=self.storage.rel_key(md5),
            storage_backend="local",
            ref_count=0,
        )
        db.add(obj)
        db.flush()
        self._ensure_object_location(db, obj)
        obj.ref_count = 1
        db.flush()
        return obj, False, created_physical

    def _ensure_object_location(self, db: Session, obj: StorageObject) -> None:
        """Ensure the local physical-location row exists and is marked PRESENT."""
        local_node_id = self._ensure_local_storage_node(db)
        now = utcnow()
        location = db.scalar(
            select(ObjectLocation).where(
                ObjectLocation.object_id == obj.object_id,
                ObjectLocation.storage_node_id == local_node_id,
                ObjectLocation.replica_role == "PRIMARY",
            )
        )
        if location is None:
            db.add(
                ObjectLocation(
                    object_id=obj.object_id,
                    storage_node_id=local_node_id,
                    replica_role="PRIMARY",
                    object_path=self.storage.rel_key(obj.content_md5),
                    status="PRESENT",
                    last_synced_at=now,
                )
            )
        else:
            location.status = "PRESENT"
            location.object_path = self.storage.rel_key(obj.content_md5)
            location.last_synced_at = now

    def _ensure_local_storage_node(self, db: Session) -> int:
        node = db.scalar(select(StorageNode).order_by(StorageNode.node_id).limit(1))
        if node is not None:
            return node.node_id
        node = StorageNode(
            hostname=socket.gethostname(),
            port=0,
            status="ONLINE",
            capacity_bytes=0,
            used_bytes=0,
            last_heartbeat_at=utcnow(),
        )
        db.add(node)
        db.flush()
        return node.node_id

    @staticmethod
    def _guess_mime(filename: str, provided: str | None) -> str:
        if provided and provided != "application/octet-stream":
            return provided[:128]
        guessed, _ = mimetypes.guess_type(filename)
        return (guessed or provided or "application/octet-stream")[:128]
