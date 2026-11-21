"""SQLite FTS5 keyword-search engine with jieba tokenisation.

The engine stores raw documents in a normal table and the jieba-tokenised
fields in an FTS5 table.  Keeping the raw text allows snippets/highlights and
a full FTS rebuild from the same database.  BM25 ranking is provided by FTS5.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from shared.documents import IndexDocumentPayload
from shared.service_types import IndexResult, SearchHit, SearchPage

from .snippet import SearchQueryError, highlight_html, make_snippet
from .tokenizer import index_text, tokenize, tokenize_phrase

logger = logging.getLogger(__name__)

__all__ = ["SearchEngine", "SearchQueryError"]


class SearchEngine:
    ENGINE_NAME = "sqlite-fts5+bm25"
    SCHEMA_VERSION = "1"

    def __init__(
        self,
        index_path: str | Path,
        *,
        title_weight: float = 8.0,
        content_weight: float = 1.0,
        tags_weight: float = 3.0,
        max_content_chars: int = 500_000,
    ) -> None:
        self.index_path = Path(index_path)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.title_weight = float(title_weight)
        self.content_weight = float(content_weight)
        self.tags_weight = float(tags_weight)
        self.max_content_chars = max_content_chars
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.index_path), check_same_thread=False, timeout=30
        )
        self._conn.row_factory = None
        self._configure()
        self._initialise_schema()
        self._repair_fts_if_needed()

    # ------------------------------------------------------------------
    # Setup / repair
    # ------------------------------------------------------------------
    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=30000")

    def _initialise_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    doc_rowid   INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id     TEXT NOT NULL UNIQUE,
                    title       TEXT NOT NULL DEFAULT '',
                    content     TEXT NOT NULL DEFAULT '',
                    tags        TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    owner_id    INTEGER NOT NULL DEFAULT 0,
                    mime_type   TEXT NOT NULL DEFAULT '',
                    updated_at  TEXT NOT NULL DEFAULT '',
                    version     INTEGER NOT NULL DEFAULT 1,
                    indexed_at  TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_search_documents_owner "
                "ON documents(owner_id)"
            )
            try:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                        title,
                        content,
                        tags,
                        tokenize = 'unicode61 remove_diacritics 2'
                    )
                    """
                )
            except sqlite3.OperationalError as exc:
                raise RuntimeError(
                    "当前 Python 的 SQLite 未启用 FTS5，无法启动关键词搜索服务"
                ) from exc

    def _repair_fts_if_needed(self) -> None:
        with self._lock:
            docs = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            indexed = self._conn.execute(
                "SELECT COUNT(*) FROM documents_fts"
            ).fetchone()[0]
            if docs != indexed:
                logger.warning(
                    "FTS index out of sync (%s documents vs %s indexed); rebuilding",
                    docs,
                    indexed,
                )
                self.rebuild_fts_from_documents()

    # ------------------------------------------------------------------
    # Index maintenance
    # ------------------------------------------------------------------
    def index_document(self, document: IndexDocumentPayload) -> IndexResult:
        file_id = str(document.file_id or "").strip()
        if not file_id:
            raise SearchQueryError("document.file_id 不能为空")

        title = (document.title or "")[:2000]
        content = (document.content or "")[: self.max_content_chars]
        tags = (document.tags or "")[:4000]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT doc_rowid FROM documents WHERE file_id = ?", (file_id,)
            ).fetchone()
            if row:
                rowid = row[0]
                self._conn.execute(
                    """
                    UPDATE documents
                       SET title = ?, content = ?, tags = ?, description = ?,
                           owner_id = ?, mime_type = ?, updated_at = ?,
                           version = ?, indexed_at = ?
                     WHERE doc_rowid = ?
                    """,
                    (
                        title,
                        content,
                        tags,
                        document.description or "",
                        int(document.owner_id or 0),
                        document.mime_type or "",
                        document.updated_at or "",
                        int(document.version or 1),
                        now,
                        rowid,
                    ),
                )
                self._conn.execute(
                    "DELETE FROM documents_fts WHERE rowid = ?", (rowid,)
                )
            else:
                cursor = self._conn.execute(
                    """
                    INSERT INTO documents
                        (file_id, title, content, tags, description, owner_id,
                         mime_type, updated_at, version, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        title,
                        content,
                        tags,
                        document.description or "",
                        int(document.owner_id or 0),
                        document.mime_type or "",
                        document.updated_at or "",
                        int(document.version or 1),
                        now,
                    ),
                )
                rowid = int(cursor.lastrowid)

            self._conn.execute(
                "INSERT INTO documents_fts(rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                (
                    rowid,
                    index_text(title),
                    index_text(content),
                    index_text(tags),
                ),
            )

        return IndexResult(
            success=True,
            message="索引已更新",
            index_version=f"{self.SCHEMA_VERSION}:{int(document.version or 1)}",
        )

    def update_document(self, document: IndexDocumentPayload) -> IndexResult:
        return self.index_document(document)

    def delete_document(self, file_id: str) -> bool:
        file_id = str(file_id or "").strip()
        if not file_id:
            return False
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT doc_rowid FROM documents WHERE file_id = ?", (file_id,)
            ).fetchone()
            if not row:
                return False
            rowid = row[0]
            self._conn.execute("DELETE FROM documents_fts WHERE rowid = ?", (rowid,))
            self._conn.execute(
                "DELETE FROM documents WHERE doc_rowid = ?", (rowid,)
            )
        return True

    def clear(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM documents_fts")
            self._conn.execute("DELETE FROM documents")

    def rebuild_fts_from_documents(self) -> int:
        """Rebuild the FTS table from raw stored documents."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT doc_rowid, title, content, tags FROM documents"
            ).fetchall()
            self._conn.execute("DELETE FROM documents_fts")
            self._conn.executemany(
                "INSERT INTO documents_fts(rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                [
                    (rowid, index_text(title), index_text(content), index_text(tags))
                    for rowid, title, content, tags in rows
                ],
            )
        return len(rows)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def _build_match_query(self, query: str, mode: str) -> tuple[str, list[str]]:
        mode = (mode or "all").lower()
        column: str | None
        if mode in {"filename", "title"}:
            column = "title"
        elif mode == "content":
            column = "content"
        elif mode in {"all", "keyword", ""}:
            column = None
        else:
            raise SearchQueryError("mode 必须是 all、filename 或 content")

        quoted = re.findall(r'"([^"]+)"', query or "")
        rest = re.sub(r'"[^"]*"', " ", query or "")

        clauses: list[str] = []
        terms: list[str] = []
        for phrase in quoted:
            tokens = tokenize_phrase(phrase)
            if tokens:
                clauses.append('"' + " ".join(tokens) + '"')
                terms.extend(tokens)
            raw = phrase.strip()
            if raw:
                terms.append(raw)

        for token in tokenize(rest):
            clauses.append(f'"{token}"')
            terms.append(token)

        # De-duplicate clauses while preserving order.
        clauses = list(dict.fromkeys(clauses))
        if not clauses:
            # Last resort: treat the whole query as one cleaned token stream.
            for token in tokenize(query or ""):
                clauses.append(f'"{token}"')
                terms.append(token)
        if not clauses:
            return "", []

        if column:
            expression = " AND ".join(f"{column} : {clause}" for clause in clauses)
        else:
            expression = " AND ".join(clauses)
        terms = list(dict.fromkeys(terms))
        return expression, terms

    def search(
        self,
        query: str,
        *,
        mode: str = "all",
        offset: int = 0,
        limit: int = 20,
        owner_id: int = 0,
    ) -> SearchPage:
        started = time.perf_counter()
        offset = max(int(offset or 0), 0)
        limit = min(max(int(limit or 20), 1), 200)

        expression, terms = self._build_match_query(query, mode)
        if not expression:
            return SearchPage(
                hits=[],
                total=0,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                engine=self.ENGINE_NAME,
            )

        where = ["documents_fts MATCH ?"]
        params: list[object] = [expression]
        if owner_id:
            where.append("d.owner_id = ?")
            params.append(int(owner_id))
        where_sql = " AND ".join(where)

        try:
            with self._lock:
                total = int(
                    self._conn.execute(
                        f"""
                        SELECT COUNT(*)
                          FROM documents_fts
                          JOIN documents d ON d.doc_rowid = documents_fts.rowid
                         WHERE {where_sql}
                        """,
                        params,
                    ).fetchone()[0]
                )
                rows = self._conn.execute(
                    f"""
                    SELECT d.file_id, d.title, d.content, d.description,
                           d.mime_type, d.updated_at, d.version,
                           bm25(documents_fts, ?, ?, ?) AS rank
                      FROM documents_fts
                      JOIN documents d ON d.doc_rowid = documents_fts.rowid
                     WHERE {where_sql}
                     ORDER BY rank ASC, d.updated_at DESC, d.doc_rowid ASC
                     LIMIT ? OFFSET ?
                    """,
                    [self.title_weight, self.content_weight, self.tags_weight]
                    + params
                    + [limit, offset],
                ).fetchall()
        except sqlite3.OperationalError as exc:
            raise SearchQueryError(f"搜索表达式无法解析: {exc}") from exc

        def choose_source(content_value: str, description_value: str, title_value: str) -> str:
            for candidate in (content_value, description_value, title_value):
                if candidate and any(term.lower() in candidate.lower() for term in terms):
                    return candidate
            return content_value or description_value or title_value or ""

        hits: list[SearchHit] = []
        for file_id, title, content, description, mime_type, updated_at, version, rank in rows:
            source = choose_source(content or "", description or "", title or "")
            hits.append(
                SearchHit(
                    file_id=str(file_id),
                    score=round(-float(rank), 6),
                    snippet=make_snippet(source, terms),
                    title=title or "",
                    mime_type=mime_type or "",
                    updated_at=updated_at or "",
                    highlight=highlight_html(source, terms),
                    version=int(version or 1),
                )
            )

        return SearchPage(
            hits=hits,
            total=total,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            engine=self.ENGINE_NAME,
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def document_count(self) -> int:
        with self._lock:
            return int(
                self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            )

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover
                pass
