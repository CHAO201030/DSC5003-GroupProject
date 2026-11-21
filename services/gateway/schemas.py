"""Pydantic request/response schemas for the public HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SearchMode = Literal["all", "filename", "content"]
RebuildKind = Literal["keyword", "semantic", "all"]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(validation_alias="user_id")
    username: str
    status: str
    created_at: datetime


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=256)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, value: str) -> str:
        return value.strip()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(validation_alias="node_id")
    owner_id: int
    parent_id: int | None
    node_type: Literal["FILE", "FOLDER"]
    name: str
    storage_object_id: int | None
    file_size: int
    mime_type: str | None
    content_md5: str | None
    version: int
    status: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class UsageOut(BaseModel):
    file_count: int
    folder_count: int
    total_bytes: int


class NodeListResponse(BaseModel):
    folder: NodeOut | None
    items: list[NodeOut]
    total: int
    page: int
    page_size: int
    has_more: bool
    usage: UsageOut


class NodeDetailResponse(BaseModel):
    node: NodeOut
    index: "IndexStatusOut | None" = None


class PathResponse(BaseModel):
    items: list[NodeOut]


class TreeNodeOut(BaseModel):
    id: int
    parent_id: int | None
    name: str
    node_type: Literal["FILE", "FOLDER"]
    file_size: int
    mime_type: str | None = None
    version: int
    updated_at: datetime | None = None
    children: list["TreeNodeOut"] = Field(default_factory=list)


class TreeResponse(BaseModel):
    root: TreeNodeOut


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None
    description: str | None = Field(default=None, max_length=2000)


class UpdateNodeRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: int | None = None


class DeleteResponse(BaseModel):
    deleted_node_ids: list[int]
    deleted_file_ids: list[int]
    freed_bytes: int


class IndexStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: int
    current_version: int
    keyword_status: Literal["PENDING", "INDEXING", "READY", "FAILED"]
    semantic_status: Literal["PENDING", "INDEXING", "READY", "FAILED"]
    keyword_index_version: int
    semantic_model_version: str | None
    retry_count: int
    last_error: str | None
    updated_at: datetime


class UploadResponse(BaseModel):
    node: NodeOut
    deduplicated: bool
    created_physical: bool
    index: IndexStatusOut | None = None


class ReplaceContentResponse(BaseModel):
    node: NodeOut
    deduplicated: bool
    index: IndexStatusOut | None = None


class SearchResultItem(BaseModel):
    file_id: int
    name: str
    node_type: str = "FILE"
    file_size: int
    mime_type: str | None
    content_md5: str | None
    updated_at: datetime
    version: int
    score: float
    snippet: str = ""
    highlight: str = ""


class KeywordSearchResponse(BaseModel):
    query: str
    mode: SearchMode
    page: int
    page_size: int
    total: int
    total_candidates: int
    results: list[SearchResultItem]
    elapsed_ms: int
    engine: str
    truncated: bool = False


class SemanticSearchResponse(BaseModel):
    query: str
    results: list[SearchResultItem]
    count: int
    elapsed_ms: int
    model_version: str = ""
    engine: str = ""
    degraded: bool = False
    message: str = ""


class SimilarFilesResponse(BaseModel):
    file_id: int
    results: list[SearchResultItem]
    count: int
    model_version: str = ""


class ServiceHealthOut(BaseModel):
    healthy: bool
    status: str
    details: str = ""
    document_count: int = 0
    model_version: str = ""
    engine: str = ""


class IndexSummaryOut(BaseModel):
    total_files: int
    keyword_ready: int
    keyword_failed: int
    keyword_pending: int
    semantic_ready: int
    semantic_failed: int
    semantic_pending: int
    search_enabled: bool
    semantic_enabled: bool
    search_service: ServiceHealthOut
    semantic_service: ServiceHealthOut


class FileIndexItem(BaseModel):
    file_id: int
    name: str
    file_size: int
    mime_type: str | None
    version: int
    updated_at: datetime
    index: IndexStatusOut


class FileIndexListResponse(BaseModel):
    items: list[FileIndexItem]
    total: int
    page: int
    page_size: int


class RebuildRequest(BaseModel):
    kinds: RebuildKind = "all"
    background: bool = False


class RebuildResponse(BaseModel):
    status: str
    files: int = 0
    keyword: dict[str, Any] | None = None
    semantic: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)


class RetryResponse(BaseModel):
    node: NodeOut
    index: IndexStatusOut


class SearchLogOut(BaseModel):
    log_id: int
    query_text: str
    search_mode: str
    result_count: int
    elapsed_ms: int
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    database: str
    services: dict[str, ServiceHealthOut]
    usage: dict[str, Any] | None = None


NodeDetailResponse.model_rebuild()
TreeNodeOut.model_rebuild()
