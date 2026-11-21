"""Semantic index: chunking + embeddings + FAISS (numpy fallback).

Vectors are L2-normalised, so inner-product search is cosine similarity.
Every chunk keeps metadata (file id, version, owner, snippet).  Deletions are
tombstones; a rebuild compacts the index.  The FAISS index and a portable
``.npy`` copy of the vectors are both persisted, which makes it possible to
recover from a damaged FAISS file or to switch backends without reindexing.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from shared.documents import IndexDocumentPayload
from shared.service_types import (
    IndexResult,
    RebuildResult,
    SemanticHit,
    SemanticPage,
    ServiceHealth,
    SimilarResult,
)

from .chunking import chunk_text
from .embedder import Embedder

logger = logging.getLogger(__name__)

try:  # optional dependency
    import faiss  # type: ignore
except Exception:  # pragma: no cover - depends on installation
    faiss = None


@dataclass(slots=True)
class _Entry:
    index_pos: int
    file_id: str
    owner_id: int
    version: int
    chunk_index: int
    text: str
    title: str = ""
    mime_type: str = ""
    updated_at: str = ""
    deleted: bool = False


class SemanticEngine:
    def __init__(
        self,
        embedder: Embedder,
        index_path: str | Path,
        meta_path: str | Path | None = None,
        *,
        chunk_size: int = 400,
        chunk_overlap: int = 80,
        max_chunks: int = 200,
        model_version_override: str = "",
        min_score: float = 0.15,
        relative_score_floor: float = 0.35,
    ) -> None:
        self.embedder = embedder
        self.dim = int(embedder.dim)
        self.model_version = (model_version_override or embedder.version).strip()
        self.index_path = Path(index_path)
        self.meta_path = Path(meta_path) if meta_path else Path(str(index_path) + ".meta.json")
        self.vectors_path = Path(str(self.index_path) + ".vectors.npy")
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_chunks = max_chunks
        self.min_score = float(min_score)
        self.relative_score_floor = max(0.0, min(float(relative_score_floor), 1.0))

        self._lock = threading.RLock()
        self._entries: list[_Entry] = []
        self._by_file: dict[str, list[_Entry]] = {}
        self._deleted_count = 0
        self._matrix = np.zeros((0, self.dim), dtype=np.float32)
        self._faiss_index = faiss.IndexFlatIP(self.dim) if faiss is not None else None
        self.load_error = ""
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @property
    def engine_name(self) -> str:
        return "faiss-index-flat-ip" if faiss is not None else "numpy-cosine"

    @property
    def degraded(self) -> bool:
        return bool(getattr(self.embedder, "degraded", False))

    def _load(self) -> None:
        if not self.meta_path.exists():
            return
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.load_error = f"索引元数据无法读取: {exc}"
            logger.warning(self.load_error)
            return

        if meta.get("model_version") != self.model_version:
            self.load_error = (
                "模型版本与已有索引不一致，索引已清空，请执行重建"
                f"（索引: {meta.get('model_version')} / 当前: {self.model_version}）"
            )
            logger.warning(self.load_error)
            return
        if int(meta.get("dim", -1)) != self.dim:
            self.load_error = "向量维度与已有索引不一致，索引已清空，请执行重建"
            logger.warning(self.load_error)
            return

        try:
            entries = [_Entry(**entry) for entry in meta.get("entries", [])]
            for position, entry in enumerate(entries):
                if int(entry.index_pos) != position:
                    raise ValueError("index_pos is not contiguous")
        except Exception as exc:
            self.load_error = f"索引元数据损坏: {exc}"
            logger.warning(self.load_error)
            return

        try:
            vectors = np.load(self.vectors_path, allow_pickle=False)
        except Exception as exc:
            self.load_error = f"向量文件无法读取，索引已清空，请执行重建: {exc}"
            logger.warning(self.load_error)
            return

        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.shape != (len(entries), self.dim):
            self.load_error = "向量文件与元数据不一致，索引已清空，请执行重建"
            logger.warning(self.load_error)
            return

        self._entries = entries
        self._matrix = vectors if vectors.size else np.zeros((0, self.dim), np.float32)
        self._reindex_metadata()

        if faiss is not None:
            try:
                loaded = faiss.read_index(str(self.index_path))
                if loaded.d != self.dim or loaded.ntotal != len(entries):
                    raise ValueError("FAISS index size mismatch")
                self._faiss_index = loaded
            except Exception as exc:
                logger.warning(
                    "FAISS 索引损坏或缺失，将从向量副本重建 (%s)", exc
                )
                self._faiss_index = faiss.IndexFlatIP(self.dim)
                if len(entries):
                    self._faiss_index.add(self._matrix)

        logger.info(
            "Loaded semantic index: %s chunks, %s live files, backend=%s",
            len(self._entries),
            self.document_count(),
            self.engine_name,
        )

    def _reindex_metadata(self) -> None:
        by_file: dict[str, list[_Entry]] = {}
        deleted = 0
        for entry in self._entries:
            by_file.setdefault(entry.file_id, []).append(entry)
            if entry.deleted:
                deleted += 1
        self._by_file = by_file
        self._deleted_count = deleted

    def _save_locked(self) -> None:
        try:
            tmp_vectors = self.vectors_path.with_name(self.vectors_path.name + ".tmp")
            with open(tmp_vectors, "wb") as handle:
                np.save(handle, self._matrix)
            os.replace(tmp_vectors, self.vectors_path)

            if faiss is not None and self._faiss_index is not None:
                tmp_index = self.index_path.with_name(self.index_path.name + ".tmp")
                faiss.write_index(self._faiss_index, str(tmp_index))
                os.replace(tmp_index, self.index_path)

            meta = {
                "schema_version": 1,
                "model_version": self.model_version,
                "dim": self.dim,
                "backend": self.engine_name,
                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "entries": [asdict(entry) for entry in self._entries],
            }
            tmp_meta = self.meta_path.with_name(self.meta_path.name + ".tmp")
            tmp_meta.write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp_meta, self.meta_path)
        except Exception:
            logger.exception("Failed to persist semantic index")

    # ------------------------------------------------------------------
    # Index maintenance
    # ------------------------------------------------------------------
    def _maybe_compact_locked(self) -> None:
        """Drop tombstones when they dominate the index.

        Automatic compaction keeps FAISS small after many re-index/delete
        operations without requiring a global (all-users) rebuild.
        """
        if self._deleted_count == 0:
            return
        if self._deleted_count < 64 and self._deleted_count * 2 < len(self._entries):
            return

        live = [entry for entry in self._entries if not entry.deleted]
        rows = [entry.index_pos for entry in live]
        matrix = (
            self._matrix[rows]
            if rows
            else np.zeros((0, self.dim), dtype=np.float32)
        )
        for position, entry in enumerate(live):
            entry.index_pos = position
        self._entries = live
        self._matrix = np.ascontiguousarray(matrix, dtype=np.float32)
        self._deleted_count = 0
        self._reindex_metadata()
        if faiss is not None:
            index = faiss.IndexFlatIP(self.dim)
            if live:
                index.add(self._matrix)
            self._faiss_index = index
        logger.info("Compacted semantic index to %s chunks", len(live))

    def _index_locked(self, document: IndexDocumentPayload) -> int:
        file_id = str(document.file_id or "").strip()
        if not file_id:
            return 0

        for entry in self._by_file.get(file_id, []):
            if not entry.deleted:
                entry.deleted = True
                self._deleted_count += 1
        self._by_file[file_id] = []

        chunks = chunk_text(
            document.semantic_text,
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap,
            max_chunks=self.max_chunks,
        )
        if not chunks:
            return 0

        vectors = self.embedder.encode(chunks)
        if vectors.shape[0] != len(chunks):
            raise RuntimeError("Embedding 数量与切片数量不一致")

        base = len(self._entries)
        new_entries = [
            _Entry(
                index_pos=base + offset,
                file_id=file_id,
                owner_id=int(document.owner_id or 0),
                version=int(document.version or 1),
                chunk_index=offset,
                text=chunk,
                title=document.title or "",
                mime_type=document.mime_type or "",
                updated_at=document.updated_at or "",
            )
            for offset, chunk in enumerate(chunks)
        ]
        self._entries.extend(new_entries)
        self._by_file[file_id] = new_entries
        self._matrix = (
            vectors
            if self._matrix.size == 0
            else np.vstack([self._matrix, vectors]).astype(np.float32)
        )
        if self._faiss_index is not None:
            self._faiss_index.add(vectors)
        return len(chunks)

    def index_document(self, document: IndexDocumentPayload) -> IndexResult:
        with self._lock:
            chunk_count = self._index_locked(document)
            self._maybe_compact_locked()
            self._save_locked()
        return IndexResult(
            success=True,
            message=f"已索引 {chunk_count} 个切片",
            index_version=f"semantic-v1:{int(document.version or 1)}:{chunk_count}",
            model_version=self.model_version,
        )

    def delete_document(self, file_id: str) -> bool:
        file_id = str(file_id or "").strip()
        found = False
        with self._lock:
            for entry in self._by_file.get(file_id, []):
                if not entry.deleted:
                    entry.deleted = True
                    self._deleted_count += 1
                    found = True
            self._by_file[file_id] = []
            if found:
                self._maybe_compact_locked()
                self._save_locked()
        return found

    def rebuild(
        self, documents: list[IndexDocumentPayload], *, clear: bool = True
    ) -> RebuildResult:
        with self._lock:
            if clear:
                self._entries = []
                self._by_file = {}
                self._deleted_count = 0
                self._matrix = np.zeros((0, self.dim), dtype=np.float32)
                self._faiss_index = (
                    faiss.IndexFlatIP(self.dim) if faiss is not None else None
                )
            chunks = 0
            for document in documents:
                chunks += self._index_locked(document)
            self._maybe_compact_locked()
            self._save_locked()
            return RebuildResult(
                success=True,
                indexed_documents=len(documents),
                indexed_chunks=chunks,
                model_version=self.model_version,
                message="语义索引重建完成",
            )

    def clear(self) -> None:
        with self._lock:
            self._entries = []
            self._by_file = {}
            self._deleted_count = 0
            self._matrix = np.zeros((0, self.dim), dtype=np.float32)
            self._faiss_index = (
                faiss.IndexFlatIP(self.dim) if faiss is not None else None
            )
            self._save_locked()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def _candidate_count(self, limit: int) -> int:
        # Enough candidates to skip every tombstone and still fill ``limit``.
        return min(len(self._entries), max(0, limit + self._deleted_count))

    def _vector_search_locked(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if k <= 0 or len(self._entries) == 0:
            return []
        if self._faiss_index is not None:
            distances, indices = self._faiss_index.search(query, k)
            return [
                (int(index), float(score))
                for index, score in zip(indices[0], distances[0])
                if index >= 0
            ]
        scores = self._matrix @ query[0]
        order = np.argsort(-scores)[:k]
        return [(int(index), float(scores[index])) for index in order]

    def search(
        self, query: str, *, limit: int = 20, owner_id: int = 0
    ) -> SemanticPage:
        started = datetime.now(timezone.utc)
        limit = min(max(int(limit or 20), 1), 200)
        if not (query or "").strip():
            return SemanticPage(
                model_version=self.model_version, engine=self.engine_name
            )

        query_vector = self.embedder.encode([query])
        if query_vector.size == 0:
            return SemanticPage(
                model_version=self.model_version, engine=self.engine_name
            )

        with self._lock:
            candidates = self._vector_search_locked(
                query_vector, self._candidate_count(limit)
            )
            candidates.sort(key=lambda item: item[1], reverse=True)
            hits: list[SemanticHit] = []
            seen: set[str] = set()
            threshold = self.min_score
            for index, score in candidates:
                if score < threshold:
                    break  # candidates are sorted by descending score
                entry = self._entries[index]
                if entry.deleted or entry.file_id in seen:
                    continue
                if owner_id and entry.owner_id != int(owner_id):
                    continue
                if not hits:
                    # Keep weak-but-relevant results while dropping the long
                    # tail of hash-collision noise relative to the best hit.
                    threshold = max(self.min_score, score * self.relative_score_floor)
                seen.add(entry.file_id)
                hits.append(
                    SemanticHit(
                        file_id=entry.file_id,
                        score=round(score, 6),
                        snippet=self._snippet(entry.text),
                        title=entry.title,
                        mime_type=entry.mime_type,
                        updated_at=entry.updated_at,
                        version=entry.version,
                    )
                )
                if len(hits) >= limit:
                    break

        elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        page = SemanticPage(
            hits=hits,
            elapsed_ms=elapsed,
            model_version=self.model_version,
            engine=self.engine_name,
            degraded=self.degraded,
        )
        if self.degraded:
            page.message = "语义服务运行在离线哈希向量降级模式，语义相似度有限"
        return page

    def similar(
        self, file_id: str, *, limit: int = 10, owner_id: int = 0
    ) -> SimilarResult:
        file_id = str(file_id or "").strip()
        limit = min(max(int(limit or 10), 1), 100)

        with self._lock:
            source_entries = [
                entry for entry in self._by_file.get(file_id, []) if not entry.deleted
            ]
            if not source_entries:
                return SimilarResult(model_version=self.model_version)
            vectors = self._matrix[[entry.index_pos for entry in source_entries]]
            mean = vectors.mean(axis=0, keepdims=True).astype(np.float32)
            norm = np.linalg.norm(mean)
            if norm == 0:
                return SimilarResult(model_version=self.model_version)
            query = mean / norm

            candidates = self._vector_search_locked(
                query, self._candidate_count(limit + 1)
            )
            candidates.sort(key=lambda item: item[1], reverse=True)
            hits: list[SemanticHit] = []
            seen: set[str] = set()
            threshold = self.min_score
            for index, score in candidates:
                if score < threshold:
                    break
                entry = self._entries[index]
                if entry.deleted or entry.file_id == file_id or entry.file_id in seen:
                    continue
                if owner_id and entry.owner_id != int(owner_id):
                    continue
                if not hits:
                    threshold = max(self.min_score, score * self.relative_score_floor)
                seen.add(entry.file_id)
                hits.append(
                    SemanticHit(
                        file_id=entry.file_id,
                        score=round(score, 6),
                        snippet=self._snippet(entry.text),
                        title=entry.title,
                        mime_type=entry.mime_type,
                        updated_at=entry.updated_at,
                        version=entry.version,
                    )
                )
                if len(hits) >= limit:
                    break

        return SimilarResult(hits=hits, model_version=self.model_version)

    @staticmethod
    def _snippet(text: str, width: int = 240) -> str:
        cleaned = " ".join((text or "").split())
        return cleaned[:width] + ("…" if len(cleaned) > width else "")

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def document_count(self) -> int:
        return sum(
            1
            for entries in self._by_file.values()
            if any(not entry.deleted for entry in entries)
        )

    def health(self) -> ServiceHealth:
        with self._lock:
            details = (
                f"chunks={len(self._entries)}, tombstones={self._deleted_count}, "
                f"files={self.document_count()}, dim={self.dim}, "
                f"embedder={self.embedder.name}"
            )
            if self.load_error:
                details += f"; load_error={self.load_error}"
            if self.degraded:
                details += "; degraded=offline-hash-embedder"
            return ServiceHealth(
                healthy=True,
                status="degraded" if self.degraded else "ok",
                details=details,
                document_count=self.document_count(),
                model_version=self.model_version,
                engine=self.engine_name,
            )
