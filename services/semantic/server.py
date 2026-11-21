"""gRPC server for the semantic search service.

Run standalone with:

    python -m services.semantic.server
"""

from __future__ import annotations

import logging
import signal
from concurrent import futures

import grpc

from clouddisk_grpc import clouddisk_pb2, clouddisk_pb2_grpc
from shared.config.settings import Settings, get_settings

from services.grpc_common import GRPC_OPTIONS
from services.proto_utils import document_from_proto

from .embedder import build_embedder
from .engine import SemanticEngine

logger = logging.getLogger(__name__)


def create_engine(settings: Settings) -> SemanticEngine:
    embedder = build_embedder(
        backend=settings.semantic_embedder,
        model_name=settings.semantic_model_name,
        cache_dir=settings.semantic_model_cache_dir,
        allow_download=settings.semantic_allow_model_download,
        hash_dim=settings.semantic_hash_dim,
    )
    return SemanticEngine(
        embedder,
        settings.semantic_index_path,
        settings.semantic_index_meta_path,
        chunk_size=settings.semantic_chunk_size,
        chunk_overlap=settings.semantic_chunk_overlap,
        max_chunks=settings.semantic_max_chunks,
        model_version_override=settings.semantic_model_version,
        min_score=settings.semantic_min_score,
        relative_score_floor=settings.semantic_relative_floor,
    )


class _SemanticServicer(clouddisk_pb2_grpc.SemanticServiceServicer):
    def __init__(self, engine: SemanticEngine) -> None:
        self.engine = engine

    def IndexDocument(self, request, context):
        try:
            result = self.engine.index_document(
                document_from_proto(request.document)
            )
        except Exception:
            logger.exception("Semantic IndexDocument failed")
            context.abort(grpc.StatusCode.INTERNAL, "语义索引写入失败")
        return clouddisk_pb2.IndexDocumentResponse(
            success=result.success,
            message=result.message,
            index_version=result.index_version,
            model_version=result.model_version,
        )

    def DeleteDocument(self, request, context):
        try:
            removed = self.engine.delete_document(request.file_id)
        except Exception:
            logger.exception("Semantic DeleteDocument failed")
            context.abort(grpc.StatusCode.INTERNAL, "语义索引删除失败")
        return clouddisk_pb2.DeleteDocumentResponse(
            success=True,
            message="语义索引已删除" if removed else "语义索引中不存在该文件",
        )

    def SemanticSearch(self, request, context):
        try:
            page = self.engine.search(
                request.query,
                limit=int(request.limit),
                owner_id=int(request.owner_id),
            )
        except Exception:
            logger.exception("SemanticSearch failed")
            context.abort(grpc.StatusCode.INTERNAL, "语义搜索失败")

        hits = [
            clouddisk_pb2.SemanticHit(
                file_id=hit.file_id,
                score=hit.score,
                snippet=hit.snippet,
                title=hit.title,
                mime_type=hit.mime_type,
                updated_at=hit.updated_at,
                version=hit.version,
            )
            for hit in page.hits
        ]
        return clouddisk_pb2.SemanticSearchResponse(
            hits=hits,
            elapsed_ms=page.elapsed_ms,
            model_version=page.model_version,
            engine=page.engine,
            degraded=page.degraded,
            message=page.message,
        )

    def SimilarFiles(self, request, context):
        try:
            result = self.engine.similar(
                request.file_id,
                limit=int(request.limit),
                owner_id=int(request.owner_id),
            )
        except Exception:
            logger.exception("SimilarFiles failed")
            context.abort(grpc.StatusCode.INTERNAL, "相似文件查询失败")

        hits = [
            clouddisk_pb2.SemanticHit(
                file_id=hit.file_id,
                score=hit.score,
                snippet=hit.snippet,
                title=hit.title,
                mime_type=hit.mime_type,
                updated_at=hit.updated_at,
                version=hit.version,
            )
            for hit in result.hits
        ]
        return clouddisk_pb2.SimilarFilesResponse(
            hits=hits, model_version=result.model_version
        )

    def RebuildIndex(self, request, context):
        try:
            documents = [
                document_from_proto(document) for document in request.documents
            ]
            result = self.engine.rebuild(documents, clear=bool(request.clear))
        except Exception:
            logger.exception("RebuildIndex failed")
            context.abort(grpc.StatusCode.INTERNAL, "语义索引重建失败")
        return clouddisk_pb2.RebuildIndexResponse(
            success=result.success,
            indexed_documents=result.indexed_documents,
            indexed_chunks=result.indexed_chunks,
            model_version=result.model_version,
            message=result.message,
        )

    def Health(self, request, context):
        health = self.engine.health()
        return clouddisk_pb2.HealthResponse(
            healthy=health.healthy,
            status=health.status,
            details=health.details,
            document_count=health.document_count,
            model_version=health.model_version,
            engine=health.engine,
        )


class SemanticGrpcServer:
    def __init__(
        self,
        engine: SemanticEngine,
        host: str = "127.0.0.1",
        port: int = 50052,
        *,
        max_workers: int = 4,
    ) -> None:
        self.engine = engine
        self.host = host
        self.port = int(port)
        self.max_workers = max_workers
        self._server: grpc.Server | None = None
        self.bound_port = 0

    def start(self) -> int:
        server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=self.max_workers),
            options=GRPC_OPTIONS,
        )
        clouddisk_pb2_grpc.add_SemanticServiceServicer_to_server(
            _SemanticServicer(self.engine), server
        )
        bound = server.add_insecure_port(f"{self.host}:{self.port}")
        if bound == 0:
            raise RuntimeError(f"无法监听语义服务端口 {self.host}:{self.port}")
        server.start()
        self._server = server
        self.bound_port = bound
        logger.info(
            "Semantic gRPC service listening on %s:%s (model=%s, engine=%s)",
            self.host,
            bound,
            self.engine.model_version,
            self.engine.engine_name,
        )
        return bound

    def wait_for_termination(self) -> None:
        if self._server is not None:
            self._server.wait_for_termination()

    def stop(self, grace: float = 2.0) -> None:
        if self._server is not None:
            self._server.stop(grace)
            self._server = None


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    settings.ensure_directories()
    engine = create_engine(settings)
    server = SemanticGrpcServer(engine, settings.semantic_host, settings.semantic_port)
    server.start()

    def _shutdown(signum, _frame):
        logger.info("Received signal %s, shutting down", signum)
        server.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    server.wait_for_termination()


if __name__ == "__main__":  # pragma: no cover
    main()
