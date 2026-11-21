from __future__ import annotations

from tests.utils import semantic_search, upload_ok

AI_TEXT = (
    "人工智能是研究机器如何模拟人类智能的学科。机器学习使用数据训练模型，"
    "深度学习使用神经网络提取特征。模型训练需要特征工程、数据清洗和评估。"
)
AI_TEXT_2 = (
    "机器学习模型依赖高质量数据和特征。神经网络通过反向传播训练，"
    "人工智能应用包括分类、预测和语义检索。"
)
COOKING_TEXT = (
    "红烧肉需要五花肉、冰糖、生姜和八角。先炒糖色，再加入热水小火炖煮，"
    "最后大火收汁。关键是控制火候和调味。"
)


def test_semantic_ranking_and_degradation_flag(client, user):
    ai = upload_ok(client, user, "人工智能.txt", AI_TEXT)
    ai2 = upload_ok(client, user, "机器学习.txt", AI_TEXT_2)
    cooking = upload_ok(client, user, "红烧肉.txt", COOKING_TEXT)
    ai_id = ai["node"]["id"]
    ai2_id = ai2["node"]["id"]
    cooking_id = cooking["node"]["id"]

    response = semantic_search(client, user, "人工智能 机器学习 模型 神经网络", top_k=10)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["degraded"] is True
    assert payload["message"]
    assert payload["model_version"].startswith("hash-ngram")
    ids = [item["file_id"] for item in payload["results"]]
    assert ai_id in ids and ai2_id in ids
    assert ids[0] in {ai_id, ai2_id}, f"unexpected ranking: {payload['results']}"
    if cooking_id in ids:
        assert ids.index(cooking_id) > 0


def test_similar_files(client, user):
    ai = upload_ok(client, user, "人工智能.txt", AI_TEXT)
    ai2 = upload_ok(client, user, "机器学习.txt", AI_TEXT_2)
    upload_ok(client, user, "红烧肉.txt", COOKING_TEXT)

    response = client.get(
        f"/api/files/{ai['node']['id']}/similar",
        headers=user.headers,
        params={"top_k": 5},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    ids = [item["file_id"] for item in data["results"]]
    assert ai2["node"]["id"] in ids
    assert ai["node"]["id"] not in ids
    assert data["model_version"].startswith("hash-ngram")


def test_semantic_delete(client, user):
    ai = upload_ok(client, user, "语义删除.txt", AI_TEXT)
    upload_ok(client, user, "语义保留.txt", AI_TEXT_2)
    assert semantic_search(client, user, "人工智能 模型", top_k=10).json()["count"] >= 1

    client.delete(f"/api/nodes/{ai['node']['id']}", headers=user.headers)
    payload = semantic_search(client, user, "人工智能 模型", top_k=10).json()
    assert ai["node"]["id"] not in [item["file_id"] for item in payload["results"]]


def test_semantic_search_validation(client, user):
    assert client.get(
        "/api/search/semantic", headers=user.headers, params={"q": ""}
    ).status_code == 422
    assert client.get(
        "/api/search/semantic", headers=user.headers, params={"q": "x", "top_k": 0}
    ).status_code == 422
