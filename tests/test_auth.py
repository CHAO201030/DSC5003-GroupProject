from __future__ import annotations

from tests.utils import download, keyword_search, register_user, upload_ok


def test_register_login_and_me(client):
    username = "auth_user_1" + __import__("uuid").uuid4().hex[:8]
    password = "secret-password"
    response = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["user"]["username"] == username
    headers = {"Authorization": f"Bearer {payload['access_token']}"}

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["username"] == username

    login = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200
    assert login.json()["access_token"]

    root = client.get("/api/nodes/root", headers=headers)
    assert root.status_code == 200
    assert root.json()["node_type"] == "FOLDER"


def test_duplicate_username_is_case_insensitive(client, user):
    response = client.post(
        "/api/auth/register",
        json={"username": user.username.upper(), "password": "another-password"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_login_failures(client, user):
    wrong = client.post(
        "/api/auth/login",
        json={"username": user.username, "password": "wrong-password"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"

    missing = client.post(
        "/api/auth/login",
        json={"username": "no-such-user-xyz", "password": "whatever"},
    )
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_password_and_username_validation(client):
    short_password = client.post(
        "/api/auth/register",
        json={"username": "valid_name", "password": "123"},
    )
    assert short_password.status_code == 422

    bad_username = client.post(
        "/api/auth/register",
        json={"username": "a!", "password": "long-enough"},
    )
    assert bad_username.status_code == 422


def test_unauthorized_requests(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/nodes/root").status_code == 401
    bad_token = {"Authorization": "Bearer not-a-real-token"}
    assert client.get("/api/auth/me", headers=bad_token).status_code == 401


def test_user_isolation(client):
    owner = register_user(client, "owner")
    intruder = register_user(client, "intruder")
    uploaded = upload_ok(
        client, owner, "私密文件.txt", "仅所有者可访问的隔离测试唯一词"
    )
    file_id = uploaded["node"]["id"]

    assert client.get(f"/api/nodes/{file_id}", headers=intruder.headers).status_code == 404
    assert download(client, intruder, file_id).status_code == 404
    assert client.delete(f"/api/nodes/{file_id}", headers=intruder.headers).status_code == 404

    found = keyword_search(client, intruder, "隔离测试唯一词", mode="content")
    assert found.status_code == 200
    assert found.json()["total"] == 0
