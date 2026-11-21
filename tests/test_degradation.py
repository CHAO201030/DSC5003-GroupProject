from __future__ import annotations

import socket
import sqlite3
from dataclasses import replace

from fastapi.testclient import TestClient

from services.gateway.app import create_app
from services.search.engine import SearchEngine
from services.semantic.embedder import HashingEmbedder
from services.semantic.engine import SemanticEngine, faiss
from shared.documents import IndexDocumentPayload
from tests.utils import (
    download,
    keyword_search,
    list_nodes,
    register_user,
    semantic_search,
    upload_ok,
)


def test_semantic_disabled_keeps_keyword_and_files_working(app_settings):
    degraded_settings = replace(app_settings, enable_semantic_service=False)
    with TestClient(create_app(degraded_settings)) as client:
        user = register_user(client, "semantic-off")
        created = upload_ok(
            client, user, "降级测试.txt", "关键词仍然可用DEGRADEKEYWORD"
        )
        index = created["index"]
        assert index["keyword_status"] == "READY"
        assert index["semantic_status"] == "FAILED"
        assert "未启用" in index["last_error"]

        keyword = keyword_search(client, user, "DEGRADEKEYWORD", mode="content")
        assert keyword.status_code == 200
        assert keyword.json()["total"] == 1

        semantic = semantic_search(client, user, "降级测试")
        assert semantic.status_code == 503
        assert semantic.json()["error"]["code"] == "SEMANTIC_UNAVAILABLE"

        assert download(client, user, created["node"]["id"]).status_code == 200
        assert list_nodes(client, user)["total"] >= 1

        summary = client.get("/api/index/status", headers=user.headers).json()
        assert summary["semantic_enabled"] is False
        assert summary["semantic_failed"] >= 1
        assert summary["keyword_ready"] >= 1


def test_search_down_does_not_break_file_management(app_settings):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        unused_port = sock.getsockname()[1]

    degraded_settings = replace(
        app_settings,
        search_port=unused_port,
        search_timeout_seconds=0.5,
        index_timeout_seconds=1.0,
        health_timeout_seconds=0.5,
    )
    with TestClient(create_app(degraded_settings)) as client:
        user = register_user(client, "search-off")
        created = upload_ok(
            client, user, "搜索宕机.txt", "搜索服务宕机时文件仍然可用SEARCHDOWN"
        )
        assert created["index"]["keyword_status"] == "FAILED"
        assert created["index"]["semantic_status"] == "READY"

        keyword = keyword_search(client, user, "SEARCHDOWN")
        assert keyword.status_code == 503
        assert keyword.json()["error"]["code"] == "SEARCH_UNAVAILABLE"

        assert download(client, user, created["node"]["id"]).status_code == 200
        assert [item["name"] for item in list_nodes(client, user)["items"]] == [
            "搜索宕机.txt"
        ]

        semantic = semantic_search(client, user, "搜索服务宕机 文件可用")
        assert semantic.status_code == 200
        assert semantic.json()["count"] >= 1


def test_corrupt_semantic_index_can_be_rebuilt(tmp_path):
    embedder = HashingEmbedder(dim=128)
    index_path = tmp_path / "semantic.index"
    meta_path = tmp_path / "semantic.meta.json"
    document = IndexDocumentPayload(
        file_id="1",
        title="机器学习",
        content="人工智能与机器学习模型，神经网络与数据分析。",
        owner_id=1,
    )

    engine = SemanticEngine(embedder, index_path, meta_path)
    engine.index_document(document)
    assert engine.search("人工智能", limit=5).hits

    vectors_path = tmp_path / "semantic.index.vectors.npy"
    vectors_path.write_bytes(b"this is not a numpy file")
    broken = SemanticEngine(embedder, index_path, meta_path)
    assert broken.load_error
    assert broken.search("人工智能", limit=5).hits == []

    rebuilt = broken.rebuild([document], clear=True)
    assert rebuilt.success
    assert broken.search("人工智能", limit=5).hits


def test_corrupt_faiss_file_recovers_from_numpy_copy(tmp_path):
    import pytest

    if faiss is None:
        pytest.skip("faiss-cpu is not installed")

    embedder = HashingEmbedder(dim=128)
    index_path = tmp_path / "faiss.index"
    meta_path = tmp_path / "faiss.meta.json"
    document = IndexDocumentPayload(
        file_id="2", title="数据库", content="数据库索引与 B+ 树结构。", owner_id=1
    )
    engine = SemanticEngine(embedder, index_path, meta_path)
    engine.index_document(document)
    assert engine.search("数据库索引", limit=5).hits

    index_path.write_bytes(b"corrupted faiss index bytes")
    recovered = SemanticEngine(embedder, index_path, meta_path)
    assert recovered.search("数据库索引", limit=5).hits
    assert recovered.load_error == ""


def test_fts_index_repairs_from_documents(tmp_path):
    index_path = tmp_path / "search.db"
    engine = SearchEngine(index_path)
    engine.index_document(
        IndexDocumentPayload(
            file_id="1",
            title="索引修复",
            content="全文检索索引修复测试语料",
            owner_id=1,
        )
    )
    assert engine.search("修复测试", limit=10).total == 1
    engine.close()

    connection = sqlite3.connect(index_path)
    connection.execute("DELETE FROM documents_fts")
    connection.commit()
    connection.close()

    repaired = SearchEngine(index_path)
    assert repaired.search("修复测试", limit=10).total == 1
    repaired.close()
