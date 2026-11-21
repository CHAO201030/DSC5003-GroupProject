"""Keyword search, semantic search and similar-file endpoints."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from services.gateway.deps import (
    get_current_user,
    get_db,
    get_search_client,
    get_semantic_client,
    get_settings,
    get_vfs,
)
from services.gateway.schemas import (
    KeywordSearchResponse,
    SearchMode,
    SearchResultItem,
    SemanticSearchResponse,
    SimilarFilesResponse,
)
from shared.config.settings import Settings
from shared.db.models import SearchLog, User, VFSNode
from shared.errors import ValidationFailed

logger = logging.getLogger(__name__)
router = APIRouter(tags=["search"])


def _item(node: VFSNode, *, score: float, snippet: str, highlight: str) -> SearchResultItem:
    return SearchResultItem(
        file_id=node.node_id,
        name=node.name,
        node_type=node.node_type,
        file_size=node.file_size,
        mime_type=node.mime_type,
        content_md5=node.content_md5,
        updated_at=node.updated_at,
        version=node.version,
        score=round(float(score), 6),
        snippet=snippet or "",
        highlight=highlight or "",
    )


def _log_search(
    db: Session,
    user_id: int,
    query: str,
    mode: str,
    result_count: int,
    elapsed_ms: int,
) -> None:
    try:
        db.add(
            SearchLog(
                user_id=user_id,
                query_text=(query or "")[:2000],
                search_mode=mode,
                result_count=max(0, int(result_count)),
                elapsed_ms=max(0, int(elapsed_ms)),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to persist search log", exc_info=True)


def _semantic_mode(mode: str) -> str:
    return {"all": "keyword", "filename": "filename", "content": "content"}.get(
        mode, "keyword"
    )


@router.get("/search/keyword", response_model=KeywordSearchResponse)
def keyword_search(
    q: str = Query(..., min_length=1, max_length=200),
    mode: SearchMode = Query(default="all"),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
    settings: Settings = Depends(get_settings),
    search_client=Depends(get_search_client),
) -> KeywordSearchResponse:
    size = min(page_size or settings.default_page_size, settings.max_page_size)
    candidate_limit = min(
        settings.search_max_candidates, max(page * size * 4, 50)
    )

    started = time.perf_counter()
    page_result = search_client.search(
        q,
        mode=mode,
        offset=0,
        limit=candidate_limit,
        owner_id=user.user_id,
    )
    nodes = vfs.active_files_by_ids(
        db, user.user_id, [hit.file_id for hit in page_result.hits]
    )

    filtered: list[SearchResultItem] = []
    for hit in page_result.hits:
        try:
            file_id = int(hit.file_id)
        except (TypeError, ValueError):
            continue
        node = nodes.get(file_id)
        if node is None:
            continue  # deleted, foreign, or otherwise no longer accessible
        filtered.append(
            _item(
                node,
                score=hit.score,
                snippet=hit.snippet,
                highlight=hit.highlight,
            )
        )

    total = len(filtered)
    start = (page - 1) * size
    results = filtered[start : start + size] if start < total else []
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    _log_search(
        db, user.user_id, q, _semantic_mode(mode), total, elapsed_ms
    )
    return KeywordSearchResponse(
        query=q,
        mode=mode,
        page=page,
        page_size=size,
        total=total,
        total_candidates=page_result.total,
        results=results,
        elapsed_ms=elapsed_ms,
        engine=page_result.engine,
        truncated=page_result.total > candidate_limit,
    )


@router.get("/search/semantic", response_model=SemanticSearchResponse)
def semantic_search(
    q: str = Query(..., min_length=1, max_length=500),
    top_k: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
    semantic_client=Depends(get_semantic_client),
) -> SemanticSearchResponse:
    started = time.perf_counter()
    page = semantic_client.search(
        q, limit=min(top_k * 3, 100), owner_id=user.user_id
    )
    nodes = vfs.active_files_by_ids(
        db, user.user_id, [hit.file_id for hit in page.hits]
    )

    results: list[SearchResultItem] = []
    for hit in page.hits:
        try:
            file_id = int(hit.file_id)
        except (TypeError, ValueError):
            continue
        node = nodes.get(file_id)
        if node is None:
            continue
        results.append(
            _item(node, score=hit.score, snippet=hit.snippet, highlight="")
        )
        if len(results) >= top_k:
            break

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _log_search(db, user.user_id, q, "semantic", len(results), elapsed_ms)
    return SemanticSearchResponse(
        query=q,
        results=results,
        count=len(results),
        elapsed_ms=elapsed_ms,
        model_version=page.model_version,
        engine=page.engine,
        degraded=page.degraded,
        message=page.message,
    )


@router.get("/files/{file_id}/similar", response_model=SimilarFilesResponse)
def similar_files(
    file_id: int,
    top_k: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vfs=Depends(get_vfs),
    semantic_client=Depends(get_semantic_client),
) -> SimilarFilesResponse:
    node = vfs.get_file_node(db, user.user_id, file_id)
    result = semantic_client.similar(
        node.node_id, limit=min(top_k * 3, 100), owner_id=user.user_id
    )
    nodes = vfs.active_files_by_ids(
        db, user.user_id, [hit.file_id for hit in result.hits]
    )
    items: list[SearchResultItem] = []
    for hit in result.hits:
        try:
            other_id = int(hit.file_id)
        except (TypeError, ValueError):
            continue
        other = nodes.get(other_id)
        if other is None or other_id == node.node_id:
            continue
        items.append(
            _item(other, score=hit.score, snippet=hit.snippet, highlight="")
        )
        if len(items) >= top_k:
            break
    return SimilarFilesResponse(
        file_id=node.node_id,
        results=items,
        count=len(items),
        model_version=result.model_version,
    )
