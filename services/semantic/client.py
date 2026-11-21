"""gRPC client for the semantic search service."""

from __future__ import annotations

import logging
import threading

import grpc

from clouddisk_grpc import clouddisk_pb2, clouddisk_pb2_grpc
from shared.documents import IndexDocumentPayload
from shared.errors import SemanticUnavailableError, ValidationFailed
from shared.service_types import (
    IndexResult,
    RebuildResult,
    SemanticHit,
    SemanticPage,
    ServiceHealth,
    SimilarResult,
)

from services.grpc_common import make_channel
from services.proto_utils import document_to_proto

logger = logging.getLogger(__name__)


class SemanticClient:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = 5.0,
        index_timeout: float = 30.0,
        enabled: bool = True,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.index_timeout = float(index_timeout)
        self.enabled = enabled
        self._lock = threading.Lock()
        self._channel: grpc.Channel | None = None
        self._stub: clouddisk_pb2_grpc.SemanticServiceStub | None = None

    def _get_stub(self) -> clouddisk_pb2_grpc.SemanticServiceStub:
        if not self.enabled:
            raise SemanticUnavailableError("语义搜索服务未启用")
        with self._lock:
            if self._stub is None:
                self._channel = make_channel(self.host, self.port)
                self._stub = clouddisk_pb2_grpc.SemanticServiceStub(self._channel)
            return self._stub

    def _call(self, method, request, timeout: float):
        try:
            return method(request, timeout=timeout)
        except grpc.RpcError as exc:
            code = exc.code()
            detail = (exc.details() or "").strip()
            if code == grpc.StatusCode.INVALID_ARGUMENT:
                raise ValidationFailed(f"语义请求不合法: {detail}") from exc
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                raise SemanticUnavailableError("语义搜索服务响应超时") from exc
            raise SemanticUnavailableError(
                f"语义搜索服务不可用（{code.name}: {detail}）"
            ) from exc

    # -- indexing ---------------------------------------------------------
    def index_document(self, document: IndexDocumentPayload) -> IndexResult:
        request = clouddisk_pb2.IndexDocumentRequest(
            document=document_to_proto(document)
        )
        response = self._call(
            self._get_stub().IndexDocument, request, self.index_timeout
        )
        return IndexResult(
            success=bool(response.success),
            message=response.message,
            index_version=response.index_version,
            model_version=response.model_version,
        )

    def delete_document(self, file_id: int | str) -> bool:
        request = clouddisk_pb2.DeleteDocumentRequest(file_id=str(file_id))
        response = self._call(
            self._get_stub().DeleteDocument, request, self.timeout
        )
        return bool(response.success)

    def rebuild(
        self, documents: list[IndexDocumentPayload], *, clear: bool = True
    ) -> RebuildResult:
        request = clouddisk_pb2.RebuildIndexRequest(
            clear=clear,
            documents=[document_to_proto(document) for document in documents],
        )
        response = self._call(
            self._get_stub().RebuildIndex, request, self.index_timeout
        )
        return RebuildResult(
            success=bool(response.success),
            indexed_documents=int(response.indexed_documents),
            indexed_chunks=int(response.indexed_chunks),
            model_version=response.model_version,
            message=response.message,
        )

    # -- query ------------------------------------------------------------
    def search(
        self, query: str, *, limit: int = 20, owner_id: int = 0
    ) -> SemanticPage:
        request = clouddisk_pb2.SemanticSearchRequest(
            query=query, limit=int(limit), owner_id=int(owner_id or 0)
        )
        response = self._call(
            self._get_stub().SemanticSearch, request, self.timeout
        )
        hits = [
            SemanticHit(
                file_id=hit.file_id,
                score=float(hit.score),
                snippet=hit.snippet,
                title=hit.title,
                mime_type=hit.mime_type,
                updated_at=hit.updated_at,
                version=int(hit.version),
            )
            for hit in response.hits
        ]
        return SemanticPage(
            hits=hits,
            elapsed_ms=int(response.elapsed_ms),
            model_version=response.model_version,
            engine=response.engine,
            degraded=bool(response.degraded),
            message=response.message,
        )

    def similar(
        self, file_id: int | str, *, limit: int = 10, owner_id: int = 0
    ) -> SimilarResult:
        request = clouddisk_pb2.SimilarFilesRequest(
            file_id=str(file_id), limit=int(limit), owner_id=int(owner_id or 0)
        )
        response = self._call(
            self._get_stub().SimilarFiles, request, self.timeout
        )
        hits = [
            SemanticHit(
                file_id=hit.file_id,
                score=float(hit.score),
                snippet=hit.snippet,
                title=hit.title,
                mime_type=hit.mime_type,
                updated_at=hit.updated_at,
                version=int(hit.version),
            )
            for hit in response.hits
        ]
        return SimilarResult(hits=hits, model_version=response.model_version)

    # -- health -----------------------------------------------------------
    def health(self, timeout: float | None = None) -> ServiceHealth:
        if not self.enabled:
            return ServiceHealth(
                healthy=False, status="disabled", details="语义搜索服务未启用"
            )
        try:
            response = self._call(
                self._get_stub().Health,
                clouddisk_pb2.HealthRequest(),
                timeout or self.timeout,
            )
            return ServiceHealth(
                healthy=bool(response.healthy),
                status=response.status,
                details=response.details,
                document_count=int(response.document_count),
                model_version=response.model_version,
                engine=response.engine,
            )
        except Exception as exc:
            logger.warning("Semantic health check failed: %s", exc)
            return ServiceHealth(healthy=False, status="unavailable", details=str(exc))

    def close(self) -> None:
        with self._lock:
            if self._channel is not None:
                self._channel.close()
            self._channel = None
            self._stub = None
