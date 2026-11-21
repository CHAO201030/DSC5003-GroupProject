"""Small transport-neutral result types shared by gRPC clients."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class IndexResult:
    success: bool
    message: str = ""
    index_version: str = ""
    model_version: str = ""


@dataclass(slots=True)
class SearchHit:
    file_id: str
    score: float
    snippet: str = ""
    title: str = ""
    mime_type: str = ""
    updated_at: str = ""
    highlight: str = ""
    version: int = 1


@dataclass(slots=True)
class SearchPage:
    hits: list[SearchHit] = field(default_factory=list)
    total: int = 0
    elapsed_ms: int = 0
    engine: str = ""
    degraded: bool = False
    message: str = ""


@dataclass(slots=True)
class SemanticHit:
    file_id: str
    score: float
    snippet: str = ""
    title: str = ""
    mime_type: str = ""
    updated_at: str = ""
    version: int = 1


@dataclass(slots=True)
class SemanticPage:
    hits: list[SemanticHit] = field(default_factory=list)
    elapsed_ms: int = 0
    model_version: str = ""
    engine: str = ""
    degraded: bool = False
    message: str = ""


@dataclass(slots=True)
class SimilarResult:
    hits: list[SemanticHit] = field(default_factory=list)
    model_version: str = ""


@dataclass(slots=True)
class ServiceHealth:
    healthy: bool
    status: str
    details: str = ""
    document_count: int = 0
    model_version: str = ""
    engine: str = ""


@dataclass(slots=True)
class RebuildResult:
    success: bool
    indexed_documents: int = 0
    indexed_chunks: int = 0
    model_version: str = ""
    message: str = ""
