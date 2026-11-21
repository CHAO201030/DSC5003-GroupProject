"""SQLAlchemy 2.x models for the CloudDisk schema.

Design notes
------------
* Logical files live in ``vfs_nodes``; physical content lives in
  ``storage_objects`` and is addressed by MD5.  Multiple logical files may
  point at the same storage object (``storage_objects.ref_count``).
* Soft deletion uses ``status`` plus ``deleted_at``/``delete_token``.  The
  active filename uniqueness constraint is
  ``(owner_id, parent_id, name, delete_token)``: active rows always use the
  token ``"0"``, deleted rows get a unique token, so MySQL can enforce the
  "one active name per folder" rule without a partial index.
* Every timestamp is naive UTC, matching MySQL ``DATETIME``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ACTIVE = "ACTIVE"
DELETED = "DELETED"
FILE = "FILE"
FOLDER = "FOLDER"

PENDING = "PENDING"
INDEXING = "INDEXING"
READY = "READY"
FAILED = "FAILED"

ACTIVE_DELETE_TOKEN = "0"

_MYSQL_TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Naive UTC timestamp used by every table."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint("status IN ('ACTIVE','DISABLED')", name="user_status"),
        _MYSQL_TABLE_ARGS,
    )

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_salt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")

    nodes: Mapped[list["VFSNode"]] = relationship(
        back_populates="owner", foreign_keys="VFSNode.owner_id"
    )
    search_logs: Mapped[list["SearchLog"]] = relationship(back_populates="user")


class StorageNode(Base, TimestampMixin):
    """Physical storage node (one local node in v1)."""

    __tablename__ = "storage_nodes"
    __table_args__ = (
        CheckConstraint("status IN ('ONLINE','OFFLINE','FULL')", name="storage_status"),
        CheckConstraint("capacity_bytes >= 0 AND used_bytes >= 0", name="storage_capacity"),
        _MYSQL_TABLE_ARGS,
    )

    node_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hostname: Mapped[str] = mapped_column(String(128), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ONLINE")
    capacity_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    locations: Mapped[list["ObjectLocation"]] = relationship(back_populates="storage_node")


class StorageObject(Base):
    """A physical object, unique per MD5."""

    __tablename__ = "storage_objects"
    __table_args__ = (
        UniqueConstraint("content_md5", name="uq_storage_objects_content_md5"),
        CheckConstraint("file_size >= 0", name="object_size"),
        CheckConstraint("ref_count >= 0", name="object_ref_count"),
        Index("ix_storage_objects_storage_key", "storage_key"),
        _MYSQL_TABLE_ARGS,
    )

    object_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_md5: Mapped[str] = mapped_column(String(32), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    ref_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    nodes: Mapped[list["VFSNode"]] = relationship(back_populates="storage_object")
    locations: Mapped[list["ObjectLocation"]] = relationship(
        back_populates="storage_object", cascade="all, delete-orphan"
    )


class ObjectLocation(Base):
    """Where a storage object is physically stored."""

    __tablename__ = "object_locations"
    __table_args__ = (
        UniqueConstraint(
            "object_id", "storage_node_id", "replica_role", name="uq_object_location"
        ),
        CheckConstraint("status IN ('PRESENT','MISSING','SYNCING')", name="location_status"),
        _MYSQL_TABLE_ARGS,
    )

    location_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_id: Mapped[int] = mapped_column(
        ForeignKey("storage_objects.object_id", ondelete="CASCADE"), nullable=False
    )
    storage_node_id: Mapped[int] = mapped_column(
        ForeignKey("storage_nodes.node_id", ondelete="CASCADE"), nullable=False
    )
    replica_role: Mapped[str] = mapped_column(String(16), nullable=False, default="PRIMARY")
    object_path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PRESENT")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    storage_object: Mapped[StorageObject] = relationship(back_populates="locations")
    storage_node: Mapped[StorageNode] = relationship(back_populates="locations")


class VFSNode(Base, TimestampMixin):
    """A logical file-system node (folder or file)."""

    __tablename__ = "vfs_nodes"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "parent_id", "name", "delete_token", name="uq_vfs_active_name"
        ),
        CheckConstraint("node_type IN ('FILE','FOLDER')", name="vfs_node_type"),
        CheckConstraint("status IN ('ACTIVE','DELETED')", name="vfs_status"),
        CheckConstraint("file_size >= 0", name="vfs_file_size"),
        CheckConstraint("version >= 1", name="vfs_version"),
        CheckConstraint(
            "(node_type = 'FOLDER' AND storage_object_id IS NULL) "
            "OR (node_type = 'FILE' AND storage_object_id IS NOT NULL)",
            name="vfs_storage_object_required",
        ),
        Index("ix_vfs_owner_parent_status", "owner_id", "parent_id", "status"),
        Index("ix_vfs_parent_name_token", "parent_id", "name", "delete_token"),
        Index("ix_vfs_content_md5", "content_md5"),
        Index("ix_vfs_updated_at", "updated_at"),
        _MYSQL_TABLE_ARGS,
    )

    node_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("vfs_nodes.node_id", ondelete="RESTRICT"), nullable=True
    )
    node_type: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage_objects.object_id", ondelete="RESTRICT"), nullable=True
    )
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_md5: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ACTIVE)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delete_token: Mapped[str] = mapped_column(
        String(64), nullable=False, default=ACTIVE_DELETE_TOKEN
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner: Mapped[User] = relationship(
        back_populates="nodes", foreign_keys=[owner_id]
    )
    parent: Mapped["VFSNode | None"] = relationship(
        back_populates="children", remote_side=[node_id]
    )
    children: Mapped[list["VFSNode"]] = relationship(back_populates="parent")
    storage_object: Mapped[StorageObject | None] = relationship(
        back_populates="nodes", foreign_keys=[storage_object_id]
    )
    index_status: Mapped["IndexStatus | None"] = relationship(
        back_populates="node", uselist=False, cascade="all, delete-orphan"
    )


class FilePermission(Base):
    __tablename__ = "file_permissions"
    __table_args__ = (
        UniqueConstraint("node_id", "user_id", name="uq_file_permission"),
        CheckConstraint(
            "permission_type IN ('READ','WRITE','OWNER')", name="permission_type"
        ),
        _MYSQL_TABLE_ARGS,
    )

    permission_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("vfs_nodes.node_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    permission_type: Mapped[str] = mapped_column(String(16), nullable=False, default="READ")
    granted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class ShareLink(Base):
    __tablename__ = "share_links"
    __table_args__ = (
        UniqueConstraint("token", name="uq_share_links_token"),
        CheckConstraint("permission IN ('READ','WRITE')", name="share_permission"),
        _MYSQL_TABLE_ARGS,
    )

    share_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        ForeignKey("vfs_nodes.node_id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str] = mapped_column(String(16), nullable=False, default="READ")
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )


class IndexStatus(Base):
    """Per-file keyword/semantic indexing state."""

    __tablename__ = "index_status"
    __table_args__ = (
        CheckConstraint(
            "keyword_status IN ('PENDING','INDEXING','READY','FAILED')",
            name="keyword_status",
        ),
        CheckConstraint(
            "semantic_status IN ('PENDING','INDEXING','READY','FAILED')",
            name="semantic_status",
        ),
        Index("ix_index_status_keyword_status", "keyword_status"),
        Index("ix_index_status_semantic_status", "semantic_status"),
        _MYSQL_TABLE_ARGS,
    )

    file_id: Mapped[int] = mapped_column(
        ForeignKey("vfs_nodes.node_id", ondelete="CASCADE"), primary_key=True
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    keyword_status: Mapped[str] = mapped_column(String(16), nullable=False, default=PENDING)
    semantic_status: Mapped[str] = mapped_column(String(16), nullable=False, default=PENDING)
    keyword_index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    semantic_model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    node: Mapped[VFSNode] = relationship(back_populates="index_status")


class SearchLog(Base):
    __tablename__ = "search_logs"
    __table_args__ = (
        CheckConstraint("search_mode IN ('keyword','filename','content','semantic')", name="search_mode"),
        CheckConstraint("result_count >= 0 AND elapsed_ms >= 0", name="search_log_metrics"),
        Index("ix_search_logs_user_created", "user_id", "created_at"),
        Index("ix_search_logs_created_at", "created_at"),
        _MYSQL_TABLE_ARGS,
    )

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    search_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="search_logs")
