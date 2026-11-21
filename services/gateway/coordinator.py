"""Indexing orchestration between VFS/MySQL and the gRPC search services.

The Gateway is the only component that combines database truth with the two
index services.  Search hits are always re-filtered through ``vfs_nodes`` in
the Gateway, so a stale index can never leak deleted or foreign files.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from shared.config.settings import Settings
from shared.db.models import (
    ACTIVE,
    FAILED,
    FILE,
    INDEXING,
    PENDING,
    READY,
    IndexStatus,
    VFSNode,
    utcnow,
)
from shared.documents import IndexDocumentPayload
from shared.errors import SearchUnavailableError, SemanticUnavailableError, ValidationFailed

logger = logging.getLogger(__name__)


class IndexCoordinator:
    def __init__(self, settings: Settings, vfs, search_client, semantic_client) -> None:
        self.settings = settings
        self.vfs = vfs
        self.search_client = search_client
        self.semantic_client = semantic_client
        self._down_until = {"search": 0.0, "semantic": 0.0}

    # ------------------------------------------------------------------
    # Single-file indexing
    # ------------------------------------------------------------------
    def _get_or_create_status(self, db: Session, node: VFSNode) -> IndexStatus:
        status = db.get(IndexStatus, node.node_id)
        if status is None:
            status = IndexStatus(
                file_id=node.node_id,
                current_version=node.version,
                keyword_status=PENDING,
                semantic_status=PENDING,
                retry_count=0,
            )
            db.add(status)
            db.flush()
        return status

    def index_file(self, db: Session, node: VFSNode) -> IndexStatus:
        if node.node_type != FILE:
            raise ValidationFailed("只有文件可以建立索引")
        status = self._get_or_create_status(db, node)
        status.current_version = node.version
        status.keyword_status = INDEXING
        status.semantic_status = INDEXING
        db.flush()

        extraction = self.vfs.extract_for_index(db, node)
        document = IndexDocumentPayload.from_node(node, extraction.text)
        errors: list[str] = []

        available, reason = self._service_available(
            "search", self.search_client, self.settings.enable_search_service
        )
        if not available:
            status.keyword_status = FAILED
            errors.append(f"keyword: {reason}")
        else:
            try:
                self.search_client.index_document(document)
                status.keyword_status = READY
                status.keyword_index_version = int(node.version or 1)
            except Exception as exc:  # service failures must not break uploads
                status.keyword_status = FAILED
                errors.append(f"keyword: {exc}")
                self._mark_down("search")
                logger.warning("Keyword indexing failed for %s: %s", node.node_id, exc)

        available, reason = self._service_available(
            "semantic", self.semantic_client, self.settings.enable_semantic_service
        )
        if not available:
            status.semantic_status = FAILED
            errors.append(f"semantic: {reason}")
        else:
            try:
                result = self.semantic_client.index_document(document)
                status.semantic_status = READY
                status.semantic_model_version = result.model_version or None
            except Exception as exc:
                status.semantic_status = FAILED
                errors.append(f"semantic: {exc}")
                self._mark_down("semantic")
                logger.warning("Semantic indexing failed for %s: %s", node.node_id, exc)

        if errors:
            status.retry_count = (status.retry_count or 0) + 1
            status.last_error = "\n".join(errors)[:4000]
        else:
            status.retry_count = 0
            status.last_error = None
        status.updated_at = utcnow()
        db.commit()
        return status

    def retry_file(self, db: Session, user_id: int, file_id: int) -> tuple[VFSNode, IndexStatus]:
        node = self.vfs.get_file_node(db, user_id, file_id)
        status = self.index_file(db, node)
        return node, status

    def delete_file_index(self, file_id: int) -> None:
        for name, client, enabled, error_cls in (
            ("search", self.search_client, self.settings.enable_search_service, SearchUnavailableError),
            ("semantic", self.semantic_client, self.settings.enable_semantic_service, SemanticUnavailableError),
        ):
            if not enabled or time.monotonic() < self._down_until[name]:
                continue
            try:
                client.delete_document(file_id)
            except error_cls as exc:
                self._mark_down(name)
                logger.info("Could not delete %s index for %s: %s", name, file_id, exc)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Unexpected %s index deletion error: %s", name, exc)

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------
    def summary(self, db: Session, user_id: int) -> dict[str, Any]:
        total_files = int(
            db.scalar(
                select(func.count(VFSNode.node_id)).where(
                    VFSNode.owner_id == user_id,
                    VFSNode.status == ACTIVE,
                    VFSNode.node_type == FILE,
                )
            )
            or 0
        )
        rows = db.execute(
            select(
                IndexStatus.keyword_status,
                IndexStatus.semantic_status,
                func.count(IndexStatus.file_id),
            )
            .join(VFSNode, VFSNode.node_id == IndexStatus.file_id)
            .where(
                VFSNode.owner_id == user_id,
                VFSNode.status == ACTIVE,
                VFSNode.node_type == FILE,
            )
            .group_by(IndexStatus.keyword_status, IndexStatus.semantic_status)
        ).all()

        keyword = {READY: 0, FAILED: 0, PENDING: 0, INDEXING: 0}
        semantic = {READY: 0, FAILED: 0, PENDING: 0, INDEXING: 0}
        covered = 0
        for keyword_status, semantic_status, count in rows:
            count = int(count)
            covered += count
            keyword[keyword_status] = keyword.get(keyword_status, 0) + count
            semantic[semantic_status] = semantic.get(semantic_status, 0) + count

        missing = max(0, total_files - covered)
        keyword[PENDING] += missing
        semantic[PENDING] += missing

        return {
            "total_files": total_files,
            "keyword_ready": keyword.get(READY, 0),
            "keyword_failed": keyword.get(FAILED, 0),
            "keyword_pending": keyword.get(PENDING, 0) + keyword.get(INDEXING, 0),
            "semantic_ready": semantic.get(READY, 0),
            "semantic_failed": semantic.get(FAILED, 0),
            "semantic_pending": semantic.get(PENDING, 0) + semantic.get(INDEXING, 0),
        }

    def list_file_statuses(
        self,
        db: Session,
        user_id: int,
        *,
        status_filter: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[tuple[VFSNode, IndexStatus]], int]:
        stmt = (
            select(VFSNode, IndexStatus)
            .join(IndexStatus, IndexStatus.file_id == VFSNode.node_id)
            .where(
                VFSNode.owner_id == user_id,
                VFSNode.status == ACTIVE,
                VFSNode.node_type == FILE,
            )
        )
        if status_filter == "failed":
            stmt = stmt.where(
                or_(
                    IndexStatus.keyword_status == FAILED,
                    IndexStatus.semantic_status == FAILED,
                )
            )
        elif status_filter == "pending":
            stmt = stmt.where(
                or_(
                    IndexStatus.keyword_status.in_([PENDING, INDEXING]),
                    IndexStatus.semantic_status.in_([PENDING, INDEXING]),
                )
            )
        elif status_filter == "ready":
            stmt = stmt.where(
                IndexStatus.keyword_status == READY,
                IndexStatus.semantic_status == READY,
            )

        total = int(
            db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        rows = db.execute(
            stmt.order_by(VFSNode.updated_at.desc(), VFSNode.node_id.desc())
            .offset(max(page - 1, 0) * page_size)
            .limit(page_size)
        ).all()
        return [(row[0], row[1]) for row in rows], total

    # ------------------------------------------------------------------
    # Rebuild
    # ------------------------------------------------------------------
    def rebuild(self, db: Session, user_id: int, kinds: str = "all") -> dict[str, Any]:
        files = list(
            db.scalars(
                select(VFSNode)
                .where(
                    VFSNode.owner_id == user_id,
                    VFSNode.status == ACTIVE,
                    VFSNode.node_type == FILE,
                )
                .order_by(VFSNode.node_id)
            ).all()
        )
        documents: list[IndexDocumentPayload] = []
        for node in files:
            extraction = self.vfs.extract_for_index(db, node)
            documents.append(IndexDocumentPayload.from_node(node, extraction.text))

        # Every file node this user ever owned.  Used to purge stale index
        # entries without touching other users' entries in the shared indexes.
        all_file_ids = list(
            db.scalars(
                select(VFSNode.node_id).where(
                    VFSNode.owner_id == user_id, VFSNode.node_type == FILE
                )
            ).all()
        )

        result: dict[str, Any] = {
            "status": "completed",
            "files": len(files),
            "keyword": None,
            "semantic": None,
            "errors": [],
        }
        kw_errors: dict[int, str] = {}
        se_errors: dict[int, str] = {}
        kw_ready: set[int] = set()
        se_ready: set[int] = set()
        semantic_model_version = ""

        # ---- keyword -------------------------------------------------
        if kinds in {"keyword", "all"}:
            available, reason = self._service_available(
                "search", self.search_client, self.settings.enable_search_service
            )
            if not available:
                result["keyword"] = {
                    "ok": False, "indexed": 0, "failed": len(files), "message": reason
                }
                for node in files:
                    kw_errors[node.node_id] = reason
            else:
                # Remove any stale entries for every file node this user ever
                # owned, then rebuild the active ones.
                for file_id in all_file_ids:
                    try:
                        self.search_client.delete_document(file_id)
                    except Exception:  # best effort
                        pass
                ok = 0
                for node, document in zip(files, documents):
                    try:
                        self.search_client.index_document(document)
                        ok += 1
                        kw_ready.add(node.node_id)
                    except Exception as exc:
                        kw_errors[node.node_id] = str(exc)
                        self._mark_down("search")
                failed = len(files) - ok
                result["keyword"] = {
                    "ok": failed == 0,
                    "indexed": ok,
                    "failed": failed,
                    "message": (
                        "关键词索引重建完成"
                        if failed == 0
                        else f"{failed} 个文件的关键词索引失败"
                    ),
                }

        # ---- semantic ------------------------------------------------
        if kinds in {"semantic", "all"}:
            available, reason = self._service_available(
                "semantic", self.semantic_client, self.settings.enable_semantic_service
            )
            if not available:
                result["semantic"] = {
                    "ok": False, "documents": 0, "chunks": 0, "message": reason
                }
                for node in files:
                    se_errors[node.node_id] = reason
            else:
                try:
                    # Delete only this user's entries, then append the rebuilt
                    # documents.  Never clear the shared index for everyone.
                    for file_id in all_file_ids:
                        try:
                            self.semantic_client.delete_document(file_id)
                        except Exception:  # best effort
                            pass
                    rebuilt = self.semantic_client.rebuild(documents, clear=False)
                    semantic_model_version = rebuilt.model_version
                    se_ready = {node.node_id for node in files}
                    result["semantic"] = {
                        "ok": True,
                        "documents": rebuilt.indexed_documents,
                        "chunks": rebuilt.indexed_chunks,
                        "message": rebuilt.message,
                    }
                except Exception as exc:
                    self._mark_down("semantic")
                    for node in files:
                        se_errors[node.node_id] = str(exc)
                    result["semantic"] = {
                        "ok": False, "documents": 0, "chunks": 0, "message": str(exc)
                    }

        # ---- per-file index_status ----------------------------------
        for node in files:
            status = self._get_or_create_status(db, node)
            status.current_version = node.version
            errors: list[str] = []
            if kinds in {"keyword", "all"}:
                if node.node_id in kw_ready:
                    status.keyword_status = READY
                    status.keyword_index_version = int(node.version or 1)
                else:
                    status.keyword_status = FAILED
                    errors.append(f"keyword: {kw_errors.get(node.node_id, '索引失败')}")
            if kinds in {"semantic", "all"}:
                if node.node_id in se_ready:
                    status.semantic_status = READY
                    status.semantic_model_version = semantic_model_version or status.semantic_model_version
                else:
                    status.semantic_status = FAILED
                    errors.append(f"semantic: {se_errors.get(node.node_id, '索引失败')}")
            if errors:
                status.retry_count = (status.retry_count or 0) + 1
                status.last_error = "\n".join(errors)[:4000]
            elif kinds == "all":
                status.retry_count = 0
                status.last_error = None
            elif kinds == "keyword" and status.semantic_status == READY:
                status.last_error = None
            elif kinds == "semantic" and status.keyword_status == READY:
                status.last_error = None
            status.updated_at = utcnow()

        db.commit()
        for bucket in ("keyword", "semantic"):
            if result[bucket] and not result[bucket].get("ok"):
                result["errors"].append(result[bucket].get("message", ""))
        return result

    # ------------------------------------------------------------------
    # Service availability (short negative cache avoids repeated timeouts)
    # ------------------------------------------------------------------
    def _mark_down(self, name: str) -> None:
        self._down_until[name] = time.monotonic() + 10.0

    def _service_available(self, name: str, client, enabled: bool) -> tuple[bool, str]:
        if not enabled:
            return False, f"{name} 索引服务未启用"
        if time.monotonic() < self._down_until[name]:
            return False, f"{name} 索引服务暂不可用（稍后自动重试）"
        health = client.health(self.settings.health_timeout_seconds)
        if health.healthy:
            return True, ""
        self._mark_down(name)
        return False, health.details or f"{name} 索引服务不可用"


def run_rebuild_background(session_factory, coordinator: IndexCoordinator, user_id: int, kinds: str) -> None:
    """BackgroundTasks entry point with its own database session."""
    db = session_factory()
    try:
        coordinator.rebuild(db, user_id, kinds)
    except Exception:
        logger.exception("Background index rebuild failed for user %s", user_id)
    finally:
        db.close()
