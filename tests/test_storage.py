from __future__ import annotations

from sqlalchemy import select

from shared.db.models import StorageObject
from tests.utils import download, replace_content, upload_ok


def _object(db, md5: str):
    db.expire_all()
    return db.scalar(select(StorageObject).where(StorageObject.content_md5 == md5))


def test_md5_dedup_reference_count_and_cleanup(client, user, app, db_session):
    content = "完全相同的内容用于验证 MD5 内容寻址与引用计数。"
    first = upload_ok(client, user, "第一份.txt", content)
    second = upload_ok(client, user, "第二份.txt", content)

    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert first["node"]["content_md5"] == second["node"]["content_md5"]
    md5 = first["node"]["content_md5"]

    obj = _object(db_session, md5)
    assert obj is not None
    assert obj.ref_count == 2
    path = app.state.vfs.storage.path_for(md5)
    assert path.is_file()

    # Renaming does not touch physical storage.
    renamed = client.patch(
        f"/api/nodes/{second['node']['id']}",
        headers=user.headers,
        json={"name": "重命名后.txt"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["node"]["content_md5"] == md5
    assert path.is_file()

    # Deleting one logical file keeps the shared physical object.
    assert client.delete(
        f"/api/nodes/{first['node']['id']}", headers=user.headers
    ).status_code == 200
    obj = _object(db_session, md5)
    assert obj is not None and obj.ref_count == 1
    assert path.is_file()
    assert download(client, user, second["node"]["id"]).content == content.encode()

    # Deleting the last reference removes the physical file.  The
    # storage_objects row is kept as content metadata for soft-deleted nodes.
    assert client.delete(
        f"/api/nodes/{second['node']['id']}", headers=user.headers
    ).status_code == 200
    obj = _object(db_session, md5)
    assert obj is not None and obj.ref_count == 0
    assert not path.exists()

    # Uploading the same content again reuses the object row and recreates
    # exactly one physical file.
    again = upload_ok(client, user, "再次上传.txt", content)
    assert again["deduplicated"] is True
    assert again["created_physical"] is True
    obj = _object(db_session, md5)
    assert obj.ref_count == 1
    assert path.is_file()


def test_different_content_creates_different_objects(client, user, db_session):
    one = upload_ok(client, user, "one.txt", "内容甲")
    two = upload_ok(client, user, "two.txt", "内容乙")
    assert one["node"]["content_md5"] != two["node"]["content_md5"]
    assert _object(db_session, one["node"]["content_md5"]).ref_count == 1
    assert _object(db_session, two["node"]["content_md5"]).ref_count == 1


def test_content_update_changes_md5_and_version(client, user, app, db_session):
    created = upload_ok(client, user, "更新.txt", "第一版内容-唯一词甲")
    file_id = created["node"]["id"]
    old_md5 = created["node"]["content_md5"]

    response = replace_content(client, user, file_id, "第二版内容-唯一词乙，长度不同")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["node"]["version"] == created["node"]["version"] + 1
    assert payload["node"]["content_md5"] != old_md5
    assert payload["index"]["current_version"] == payload["node"]["version"]

    downloaded = download(client, user, file_id)
    assert downloaded.content.decode("utf-8") == "第二版内容-唯一词乙，长度不同"

    old_obj = _object(db_session, old_md5)
    assert old_obj is not None and old_obj.ref_count == 0
    assert not app.state.vfs.storage.path_for(old_md5).exists()
    new_obj = _object(db_session, payload["node"]["content_md5"])
    assert new_obj is not None and new_obj.ref_count == 1
    assert app.state.vfs.storage.path_for(payload["node"]["content_md5"]).is_file()


def test_update_reuses_existing_object(client, user, app, db_session):
    alpha = upload_ok(client, user, "alpha.txt", "alpha 内容")
    beta = upload_ok(client, user, "beta.txt", "beta 内容")
    alpha_md5 = alpha["node"]["content_md5"]
    beta_md5 = beta["node"]["content_md5"]

    response = replace_content(client, user, alpha["node"]["id"], "beta 内容")
    assert response.status_code == 200
    assert response.json()["node"]["content_md5"] == beta_md5
    assert response.json()["deduplicated"] is True

    beta_obj = _object(db_session, beta_md5)
    assert beta_obj.ref_count == 2
    alpha_obj = _object(db_session, alpha_md5)
    assert alpha_obj is not None and alpha_obj.ref_count == 0
    assert not app.state.vfs.storage.path_for(alpha_md5).exists()


def test_empty_file_and_duplicate_names(client, user):
    empty = upload_ok(client, user, "empty.txt", b"")
    assert empty["node"]["file_size"] == 0
    assert empty["node"]["content_md5"] == "d41d8cd98f00b204e9800998ecf8427e"
    assert download(client, user, empty["node"]["id"]).content == b""

    duplicate_name = client.post(
        "/api/files/upload",
        headers=user.headers,
        files={"file": ("empty.txt", b"different", "text/plain")},
    )
    assert duplicate_name.status_code == 409
