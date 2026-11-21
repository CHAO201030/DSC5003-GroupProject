from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from shared.db.models import IndexStatus, SearchLog, StorageObject, VFSNode
from tests.utils import create_folder, keyword_search, upload_ok


def test_unique_username_and_filename(client, user):
    from tests.utils import register_user

    duplicate = client.post(
        "/api/auth/register",
        json={"username": user.username, "password": "another-password"},
    )
    assert duplicate.status_code == 409

    upload_ok(client, user, "same-name.txt", "first")
    conflict = client.post(
        "/api/files/upload",
        headers=user.headers,
        files={"file": ("same-name.txt", b"second", "text/plain")},
    )
    assert conflict.status_code == 409

    create_folder(client, user, "name-clash")
    folder_file_conflict = client.post(
        "/api/files/upload",
        headers=user.headers,
        files={"file": ("name-clash", b"third", "text/plain")},
    )
    assert folder_file_conflict.status_code == 409

    another = register_user(client, "another-owner")
    assert another.user_id != user.user_id


def test_deleted_name_can_be_reused(client, user):
    first = upload_ok(client, user, "reuse.txt", "first version")
    client.delete(f"/api/nodes/{first['node']['id']}", headers=user.headers)
    second = upload_ok(client, user, "reuse.txt", "second version")
    assert second["node"]["name"] == "reuse.txt"


def test_file_requires_storage_object(client, user, db_session):
    db_session.expire_all()
    node = VFSNode(
        owner_id=user.user_id,
        parent_id=user.root_id,
        node_type="FILE",
        name="invalid-file-without-object",
        file_size=0,
        version=1,
        status="ACTIVE",
        delete_token="0",
    )
    db_session.add(node)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_folder_cannot_reference_storage_object(client, user, db_session):
    created = upload_ok(client, user, "object-holder.txt", "holder")
    db_session.expire_all()
    obj = db_session.scalar(
        select(StorageObject).where(
            StorageObject.content_md5 == created["node"]["content_md5"]
        )
    )
    node = VFSNode(
        owner_id=user.user_id,
        parent_id=user.root_id,
        node_type="FOLDER",
        name="invalid-folder-with-object",
        storage_object_id=obj.object_id,
        file_size=0,
        version=1,
        status="ACTIVE",
        delete_token="0",
    )
    db_session.add(node)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_reference_count_and_size_are_non_negative(client, user, db_session):
    created = upload_ok(client, user, "ref.txt", "ref count")
    db_session.expire_all()
    obj = db_session.scalar(
        select(StorageObject).where(
            StorageObject.content_md5 == created["node"]["content_md5"]
        )
    )
    obj.ref_count = -1
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.expire_all()
    obj = db_session.scalar(
        select(StorageObject).where(
            StorageObject.content_md5 == created["node"]["content_md5"]
        )
    )
    obj.file_size = -100
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_md5_storage_object_is_unique(client, user, db_session):
    created = upload_ok(client, user, "unique-md5.txt", "unique md5 content")
    db_session.expire_all()
    existing = db_session.scalar(
        select(StorageObject).where(
            StorageObject.content_md5 == created["node"]["content_md5"]
        )
    )
    duplicate = StorageObject(
        content_md5=existing.content_md5,
        file_size=existing.file_size,
        storage_key="duplicate/path",
        storage_backend="local",
        ref_count=0,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_index_status_values_are_limited(client, user, db_session):
    created = upload_ok(client, user, "status.txt", "status check")
    db_session.expire_all()
    status = db_session.get(IndexStatus, created["node"]["id"])
    status.keyword_status = "BROKEN"
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_parent_foreign_key_enforced(client, user, db_session):
    db_session.expire_all()
    node = VFSNode(
        owner_id=user.user_id,
        parent_id=9_999_999,
        node_type="FOLDER",
        name="invalid-parent",
        file_size=0,
        version=1,
        status="ACTIVE",
        delete_token="0",
    )
    db_session.add(node)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_search_log_rows(client, user, db_session):
    upload_ok(client, user, "log.txt", "搜索日志持久化测试")
    response = keyword_search(client, user, "日志持久化", mode="content")
    assert response.status_code == 200

    db_session.expire_all()
    count = db_session.scalar(
        select(func.count(SearchLog.log_id)).where(
            SearchLog.user_id == user.user_id
        )
    )
    assert count and count >= 1
