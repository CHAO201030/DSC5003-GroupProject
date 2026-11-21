from __future__ import annotations

from shared.db.models import IndexStatus
from tests.utils import (
    keyword_search,
    replace_content,
    semantic_search,
    upload_ok,
)


def test_upload_creates_ready_index_status(client, user):
    created = upload_ok(client, user, "索引.txt", "索引状态测试语料")
    index = created["index"]
    assert index["keyword_status"] == "READY", created
    assert index["semantic_status"] == "READY", created
    assert index["current_version"] == 1

    summary = client.get("/api/index/status", headers=user.headers)
    assert summary.status_code == 200
    data = summary.json()
    assert data["total_files"] == 1
    assert data["keyword_ready"] == 1
    assert data["semantic_ready"] == 1
    assert data["search_service"]["healthy"] is True
    assert data["semantic_service"]["healthy"] is True


def test_rename_reindexes_title(client, user):
    created = upload_ok(client, user, "旧名称.txt", "与名称无关的正文内容")
    file_id = created["node"]["id"]
    assert keyword_search(client, user, "旧名称", mode="filename").json()["total"] == 1

    renamed = client.patch(
        f"/api/nodes/{file_id}", headers=user.headers, json={"name": "新名称.txt"}
    )
    assert renamed.status_code == 200
    assert keyword_search(client, user, "旧名称", mode="filename").json()["total"] == 0
    assert keyword_search(client, user, "新名称", mode="filename").json()["total"] == 1


def test_retry_failed_index(client, user, db_session):
    created = upload_ok(client, user, "重试.txt", "重试测试语料，关键词重试XYZ")
    file_id = created["node"]["id"]

    db_session.expire_all()
    status = db_session.get(IndexStatus, file_id)
    status.keyword_status = "FAILED"
    status.semantic_status = "FAILED"
    status.last_error = "模拟故障"
    db_session.commit()

    response = client.post(f"/api/index/{file_id}/retry", headers=user.headers)
    assert response.status_code == 200, response.text
    payload = response.json()["index"]
    assert payload["keyword_status"] == "READY"
    assert payload["semantic_status"] == "READY"
    assert payload["last_error"] is None
    assert payload["retry_count"] == 0
    assert keyword_search(client, user, "重试XYZ", mode="content").json()["total"] == 1


def test_rebuild_keyword_index(client, user, search_server):
    created = upload_ok(client, user, "重建.txt", "关键词重建独有语料KEYWORDREBUILD")
    file_id = created["node"]["id"]
    assert keyword_search(client, user, "KEYWORDREBUILD", mode="content").json()["total"] == 1

    search_server.engine.delete_document(str(file_id))
    assert keyword_search(client, user, "KEYWORDREBUILD", mode="content").json()["total"] == 0

    rebuilt = client.post(
        "/api/index/rebuild", headers=user.headers, json={"kinds": "keyword"}
    )
    assert rebuilt.status_code == 200, rebuilt.text
    data = rebuilt.json()
    assert data["keyword"]["ok"] is True
    assert data["keyword"]["indexed"] >= 1
    assert keyword_search(client, user, "KEYWORDREBUILD", mode="content").json()["total"] == 1


def test_rebuild_semantic_index(client, user, semantic_server):
    created = upload_ok(
        client,
        user,
        "语义重建.txt",
        "语义重建测试：人工智能、机器学习、神经网络与模型训练。",
    )
    file_id = created["node"]["id"]
    assert semantic_search(client, user, "人工智能 模型", top_k=10).json()["count"] >= 1

    semantic_server.engine.delete_document(str(file_id))
    assert semantic_search(client, user, "人工智能 模型", top_k=10).json()["count"] == 0

    rebuilt = client.post(
        "/api/index/rebuild", headers=user.headers, json={"kinds": "semantic"}
    )
    assert rebuilt.status_code == 200, rebuilt.text
    data = rebuilt.json()
    assert data["semantic"]["ok"] is True
    assert data["semantic"]["chunks"] >= 1
    assert semantic_search(client, user, "人工智能 模型", top_k=10).json()["count"] >= 1


def test_file_status_filters(client, user, db_session):
    created = upload_ok(client, user, "筛选.txt", "索引筛选测试语料")
    file_id = created["node"]["id"]
    db_session.expire_all()
    status = db_session.get(IndexStatus, file_id)
    status.keyword_status = "FAILED"
    status.last_error = "测试错误"
    db_session.commit()

    failed = client.get(
        "/api/index/files", headers=user.headers, params={"status_filter": "failed"}
    )
    assert failed.status_code == 200
    assert any(item["file_id"] == file_id for item in failed.json()["items"])
    assert "测试错误" in failed.json()["items"][0]["index"]["last_error"]

    ready = client.get(
        "/api/index/files", headers=user.headers, params={"status_filter": "ready"}
    )
    assert all(item["file_id"] != file_id for item in ready.json()["items"])


def test_content_update_resets_index_version(client, user):
    created = upload_ok(client, user, "版本.txt", "版本一独有语料VERSIONONE")
    file_id = created["node"]["id"]
    assert keyword_search(client, user, "VERSIONONE", mode="content").json()["total"] == 1

    response = replace_content(client, user, file_id, "版本二独有语料VERSIONTWO")
    assert response.status_code == 200
    index = response.json()["index"]
    assert index["current_version"] == 2
    assert index["keyword_status"] == "READY"
    assert index["semantic_status"] == "READY"

    assert keyword_search(client, user, "VERSIONONE", mode="content").json()["total"] == 0
    assert keyword_search(client, user, "VERSIONTWO", mode="content").json()["total"] == 1


def test_rebuild_validation(client, user):
    assert client.post(
        "/api/index/rebuild", headers=user.headers, json={"kinds": "invalid"}
    ).status_code == 422
    assert client.post(
        "/api/index/99999999/retry", headers=user.headers
    ).status_code == 404
