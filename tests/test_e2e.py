from __future__ import annotations

from tests.utils import (
    create_folder,
    download,
    keyword_search,
    list_nodes,
    register_user,
    semantic_search,
    upload_ok,
)

E2E_AI = (
    "端到端测试文档：人工智能与机器学习。模型使用数据训练，"
    "深度学习通过神经网络提取特征，并进行语义检索。端到端独特词E2EONLY。"
)
E2E_AI_2 = (
    "端到端测试文档二：机器学习模型、人工神经网络、数据特征和语义搜索。"
)


def test_frontend_and_docs_are_served(client):
    root = client.get("/")
    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
    assert "CloudDisk" in root.text
    assert 'id="auth-view"' in root.text

    css = client.get("/styles.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]

    js = client.get("/app.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]

    docs = client.get("/api/docs")
    assert docs.status_code == 200
    assert "text/html" in docs.headers["content-type"]

    openapi = client.get("/api/openapi.json")
    assert openapi.status_code == 200
    assert "/api/auth/login" in openapi.json()["paths"]

    health = client.get("/api/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["database"] == "ok"
    assert set(payload["services"]) == {"search", "semantic"}


def test_full_user_journey(client):
    user = register_user(client, "e2e")
    folder = create_folder(client, user, "端到端资料")
    sub = create_folder(client, user, "归档", parent_id=folder["id"])

    ai = upload_ok(
        client,
        user,
        "人工智能.md",
        E2E_AI,
        parent_id=folder["id"],
        mime_type="text/markdown; charset=utf-8",
    )
    ai2 = upload_ok(
        client, user, "机器学习.txt", E2E_AI_2, parent_id=folder["id"]
    )
    assert ai["index"]["keyword_status"] == "READY"
    assert ai["index"]["semantic_status"] == "READY"

    listing = list_nodes(client, user, folder["id"])
    # Folders are listed first, then files by name.
    assert [item["name"] for item in listing["items"]] == [
        "归档",
        "人工智能.md",
        "机器学习.txt",
    ]

    downloaded = download(client, user, ai["node"]["id"])
    assert downloaded.status_code == 200
    assert downloaded.content.decode("utf-8") == E2E_AI
    assert downloaded.headers["X-Content-MD5"] == ai["node"]["content_md5"]

    by_name = keyword_search(client, user, "人工智能", mode="filename")
    assert by_name.json()["total"] == 1

    by_body = keyword_search(client, user, "端到端独特词E2EONLY", mode="content")
    assert by_body.json()["total"] == 1
    assert by_body.json()["results"][0]["file_id"] == ai["node"]["id"]

    semantic = semantic_search(client, user, "人工智能 机器学习 神经网络", top_k=10)
    assert semantic.status_code == 200
    semantic_ids = [item["file_id"] for item in semantic.json()["results"]]
    assert ai["node"]["id"] in semantic_ids
    assert semantic.json()["degraded"] is True

    similar = client.get(
        f"/api/files/{ai['node']['id']}/similar",
        headers=user.headers,
        params={"top_k": 5},
    )
    assert similar.status_code == 200
    assert ai2["node"]["id"] in [item["file_id"] for item in similar.json()["results"]]

    renamed = client.patch(
        f"/api/nodes/{ai2['node']['id']}",
        headers=user.headers,
        json={"name": "机器学习笔记.txt"},
    )
    assert renamed.status_code == 200
    assert keyword_search(client, user, "人工智能", mode="filename").json()["total"] == 1
    assert keyword_search(client, user, "机器学习笔记", mode="filename").json()["total"] == 1

    moved = client.patch(
        f"/api/nodes/{ai['node']['id']}",
        headers=user.headers,
        json={"parent_id": sub["id"]},
    )
    assert moved.status_code == 200
    assert [item["name"] for item in list_nodes(client, user, sub["id"])["items"]] == [
        "人工智能.md"
    ]

    index_status = client.get("/api/index/status", headers=user.headers)
    assert index_status.status_code == 200
    summary = index_status.json()
    assert summary["total_files"] == 2
    assert summary["keyword_ready"] == 2
    assert summary["semantic_ready"] == 2

    deleted = client.delete(
        f"/api/nodes/{folder['id']}", headers=user.headers, params={"recursive": "true"}
    )
    assert deleted.status_code == 200
    assert set(deleted.json()["deleted_file_ids"]) == {
        ai["node"]["id"], ai2["node"]["id"]
    }

    assert download(client, user, ai["node"]["id"]).status_code == 404
    assert keyword_search(client, user, "端到端独特词E2EONLY", mode="content").json()["total"] == 0
    assert semantic_search(client, user, "端到端测试文档", top_k=10).json()["count"] == 0
    assert all(item["name"] != "端到端资料" for item in list_nodes(client, user)["items"])
