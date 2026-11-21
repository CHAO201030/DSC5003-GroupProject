"""gRPC server for the keyword search service.

Run standalone with:

    python -m services.search.server
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

from .engine import SearchEngine, SearchQueryError

logger = logging.getLogger(__name__)


class _SearchServicer(clouddisk_pb2_grpc.SearchServiceServicer):
    def __init__(self, engine: SearchEngine) -> None:
        self.engine = engine

    def IndexDocument(self, request, context):
        try:
            result = self.engine.index_document(
                document_from_proto(request.document)
            )
        except SearchQueryError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception:
            logger.exception("IndexDocument failed")
            context.abort(grpc.StatusCode.INTERNAL, "索引写入失败")
        return clouddisk_pb2.IndexDocumentResponse(
            success=result.success,
            message=result.message,
            index_version=result.index_version,
            model_version=result.model_version,
        )

    def UpdateDocument(self, request, context):
        try:
            result = self.engine.update_document(
                document_from_proto(request.document)
            )
        except SearchQueryError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception:
            logger.exception("UpdateDocument failed")
            context.abort(grpc.StatusCode.INTERNAL, "索引更新失败")
        return clouddisk_pb2.UpdateDocumentResponse(
            success=result.success, message=result.message
        )

    def DeleteDocument(self, request, context):
        try:
            removed = self.engine.delete_document(request.file_id)
        except Exception:
            logger.exception("DeleteDocument failed")
            context.abort(grpc.StatusCode.INTERNAL, "索引删除失败")
        return clouddisk_pb2.DeleteDocumentResponse(
            success=True,
            message="索引已删除" if removed else "索引中不存在该文件",
        )

    def Search(self, request, context):
        try:
            page = self.engine.search(
                request.query,
                mode=request.mode or "all",
                offset=int(request.offset),
                limit=int(request.limit),
                owner_id=int(request.owner_id),
            )
        except SearchQueryError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception:
            logger.exception("Search failed")
            context.abort(grpc.StatusCode.INTERNAL, "搜索失败")

        hits = [
            clouddisk_pb2.SearchHit(
                file_id=hit.file_id,
                score=hit.score,
                snippet=hit.snippet,
                title=hit.title,
                mime_type=hit.mime_type,
                updated_at=hit.updated_at,
                highlight=hit.highlight,
                version=hit.version,
            )
            for hit in page.hits
        ]
        return clouddisk_pb2.SearchResponse(
            hits=hits,
            total=page.total,
            elapsed_ms=page.elapsed_ms,
            engine=page.engine,
            degraded=page.degraded,
            message=page.message,
        )

    def Health(self, request, context):
        try:
            count = self.engine.document_count()
            return clouddisk_pb2.HealthResponse(
                healthy=True,
                status="ok",
                details=f"index={self.engine.index_path}",
                document_count=count,
                engine=self.engine.ENGINE_NAME,
            )
        except Exception as exc:
            logger.exception("Search health failed")
            return clouddisk_pb2.HealthResponse(
                healthy=False, status="error", details=str(exc)
            )


class SearchGrpcServer:
    def __init__(
        self,
        engine: SearchEngine,
        host: str = "127.0.0.1",
        port: int = 50051,
        *,
        max_workers: int = 8,
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
        clouddisk_pb2_grpc.add_SearchServiceServicer_to_server(
            _SearchServicer(self.engine), server
        )
        bound = server.add_insecure_port(f"{self.host}:{self.port}")
        if bound == 0:
            raise RuntimeError(f"无法监听关键词搜索服务端口 {self.host}:{self.port}")
        server.start()
        self._server = server
        self.bound_port = bound
        logger.info(
            "Search gRPC service listening on %s:%s (engine=%s)",
            self.host,
            bound,
            self.engine.ENGINE_NAME,
        )
        return bound

    def wait_for_termination(self) -> None:
        if self._server is not None:
            self._server.wait_for_termination()

    def stop(self, grace: float = 2.0) -> None:
        if self._server is not None:
            self._server.stop(grace)
            self._server = None
        try:
            self.engine.close()
        except Exception:  # pragma: no cover
            pass


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings: Settings = get_settings()
    settings.ensure_directories()
    engine = SearchEngine(
        settings.search_index_path,
        title_weight=settings.search_title_weight,
        content_weight=settings.search_content_weight,
        tags_weight=settings.search_tags_weight,
        max_content_chars=settings.max_index_chars,
    )
    server = SearchGrpcServer(engine, settings.search_host, settings.search_port)
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
