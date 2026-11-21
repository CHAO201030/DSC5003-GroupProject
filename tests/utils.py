"""Helpers shared by the CloudDisk tests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient


@dataclass
class TestUser:
    username: str
    password: str
    user_id: int
    token: str
    headers: dict[str, str]
    root_id: int


def register_user(client: TestClient, prefix: str = "user") -> TestUser:
    username = f"{prefix}_{uuid.uuid4().hex[:12]}"
    password = "test-password-123"
    response = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    headers = {"Authorization": f"Bearer {payload['access_token']}"}
    root = client.get("/api/nodes/root", headers=headers)
    assert root.status_code == 200, root.text
    return TestUser(
        username=username,
        password=password,
        user_id=int(payload["user"]["id"]),
        token=payload["access_token"],
        headers=headers,
        root_id=int(root.json()["id"]),
    )


def create_folder(
    client: TestClient,
    user: TestUser,
    name: str,
    parent_id: int | None = None,
) -> dict:
    response = client.post(
        "/api/folders",
        headers=user.headers,
        json={"name": name, "parent_id": parent_id if parent_id is not None else user.root_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(
    client: TestClient,
    user: TestUser,
    filename: str,
    content: str | bytes,
    *,
    parent_id: int | None = None,
    mime_type: str = "text/plain; charset=utf-8",
):
    if isinstance(content, str):
        content = content.encode("utf-8")
    data = {}
    if parent_id is not None:
        data["parent_id"] = str(parent_id)
    return client.post(
        "/api/files/upload",
        headers=user.headers,
        data=data,
        files={"file": (filename, content, mime_type)},
    )


def upload_ok(
    client: TestClient,
    user: TestUser,
    filename: str,
    content: str | bytes,
    *,
    parent_id: int | None = None,
    mime_type: str = "text/plain; charset=utf-8",
) -> dict:
    response = upload(
        client,
        user,
        filename,
        content,
        parent_id=parent_id,
        mime_type=mime_type,
    )
    assert response.status_code == 201, response.text
    return response.json()


def replace_content(
    client: TestClient,
    user: TestUser,
    file_id: int,
    content: str | bytes,
    *,
    mime_type: str = "text/plain; charset=utf-8",
):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return client.put(
        f"/api/files/{file_id}/content",
        headers=user.headers,
        files={"file": ("updated.txt", content, mime_type)},
    )


def keyword_search(
    client: TestClient,
    user: TestUser,
    query: str,
    *,
    mode: str = "all",
    page: int = 1,
    page_size: int = 20,
):
    return client.get(
        "/api/search/keyword",
        headers=user.headers,
        params={"q": query, "mode": mode, "page": page, "page_size": page_size},
    )


def semantic_search(client: TestClient, user: TestUser, query: str, top_k: int = 20):
    return client.get(
        "/api/search/semantic",
        headers=user.headers,
        params={"q": query, "top_k": top_k},
    )


def download(client: TestClient, user: TestUser, file_id: int):
    return client.get(f"/api/files/{file_id}/download", headers=user.headers)


def list_nodes(
    client: TestClient, user: TestUser, parent_id: int | None = None
) -> dict:
    params = {"page_size": 200}
    if parent_id is not None:
        params["parent_id"] = parent_id
    response = client.get("/api/nodes", headers=user.headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()
