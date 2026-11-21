from __future__ import annotations

from tests.utils import (
    create_folder,
    download,
    keyword_search,
    list_nodes,
    upload_ok,
)


def test_folder_listing_path_and_tree(client, user):
    docs = create_folder(client, user, "文档")
    sub = create_folder(client, user, "子目录", parent_id=docs["id"])
    note = upload_ok(client, user, "笔记.txt", "笔记内容", parent_id=sub["id"])

    root_listing = list_nodes(client, user)
    assert root_listing["folder"]["id"] == user.root_id
    assert root_listing["items"][0]["name"] == "文档"
    assert root_listing["items"][0]["node_type"] == "FOLDER"

    sub_listing = list_nodes(client, user, parent_id=sub["id"])
    assert [item["name"] for item in sub_listing["items"]] == ["笔记.txt"]

    path = client.get(f"/api/nodes/{note['node']['id']}/path", headers=user.headers)
    assert path.status_code == 200
    assert [item["name"] for item in path.json()["items"]] == ["我的文件", "文档", "子目录", "笔记.txt"]

    tree = client.get(
        "/api/nodes/tree",
        headers=user.headers,
        params={"root_id": user.root_id, "depth": 5},
    )
    assert tree.status_code == 200
    root = tree.json()["root"]
    assert root["children"][0]["name"] == "文档"
    assert root["children"][0]["children"][0]["name"] == "子目录"

    shallow = client.get(
        "/api/nodes/tree",
        headers=user.headers,
        params={"root_id": user.root_id, "depth": 0},
    )
    assert shallow.json()["root"]["children"] == []


def test_rename_conflict_and_success(client, user):
    first = upload_ok(client, user, "a.txt", "aaa")
    second = upload_ok(client, user, "b.txt", "bbb")

    conflict = client.patch(
        f"/api/nodes/{second['node']['id']}",
        headers=user.headers,
        json={"name": "a.txt"},
    )
    assert conflict.status_code == 409

    renamed = client.patch(
        f"/api/nodes/{second['node']['id']}",
        headers=user.headers,
        json={"name": "renamed.txt"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["node"]["name"] == "renamed.txt"
    assert renamed.json()["node"]["content_md5"] == second["node"]["content_md5"]

    # Rename is reflected in the keyword index.
    assert keyword_search(client, user, "renamed", mode="filename").json()["total"] == 1
    assert keyword_search(client, user, "b.txt", mode="filename").json()["total"] == 0
    assert first["node"]["id"] != second["node"]["id"]


def test_move_file_and_forbid_folder_cycle(client, user):
    folder = create_folder(client, user, "目标")
    file_node = upload_ok(client, user, "移动.txt", "move me")

    moved = client.patch(
        f"/api/nodes/{file_node['node']['id']}",
        headers=user.headers,
        json={"parent_id": folder["id"]},
    )
    assert moved.status_code == 200
    assert moved.json()["node"]["parent_id"] == folder["id"]
    assert [n["name"] for n in list_nodes(client, user, folder["id"])["items"]] == ["移动.txt"]

    parent = create_folder(client, user, "父目录")
    child = create_folder(client, user, "子目录", parent_id=parent["id"])
    cycle = client.patch(
        f"/api/nodes/{parent['id']}",
        headers=user.headers,
        json={"parent_id": child["id"]},
    )
    assert cycle.status_code == 422


def test_soft_delete_folder_recursive(client, user):
    folder = create_folder(client, user, "待删除")
    sub = create_folder(client, user, "子文件夹", parent_id=folder["id"])
    file_node = upload_ok(
        client, user, "删除我.txt", "递归删除测试唯一内容", parent_id=sub["id"]
    )
    file_id = file_node["node"]["id"]

    non_recursive = client.delete(
        f"/api/nodes/{folder['id']}",
        headers=user.headers,
        params={"recursive": "false"},
    )
    assert non_recursive.status_code == 409

    deleted = client.delete(
        f"/api/nodes/{folder['id']}", headers=user.headers, params={"recursive": "true"}
    )
    assert deleted.status_code == 200
    payload = deleted.json()
    assert file_id in payload["deleted_file_ids"]
    assert len(payload["deleted_node_ids"]) == 3

    assert client.get(f"/api/nodes/{folder['id']}", headers=user.headers).status_code == 404
    assert download(client, user, file_id).status_code == 404
    assert keyword_search(client, user, "递归删除测试唯一内容", mode="content").json()["total"] == 0

    # The name can be reused after soft deletion.
    recreated = create_folder(client, user, "待删除")
    assert recreated["name"] == "待删除"


def test_root_protection(client, user):
    rename = client.patch(
        f"/api/nodes/{user.root_id}", headers=user.headers, json={"name": "new-root"}
    )
    assert rename.status_code == 422
    delete = client.delete(f"/api/nodes/{user.root_id}", headers=user.headers)
    assert delete.status_code == 422

    moved = client.patch(
        f"/api/nodes/{user.root_id}",
        headers=user.headers,
        json={"parent_id": user.root_id},
    )
    assert moved.status_code == 422
