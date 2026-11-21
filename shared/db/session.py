"""Engine/session helpers.

The production database is MySQL 8 (PyMySQL).  SQLite is supported for
zero-dependency local trials and for the automated test-suite; the SQLAlchemy
models and the application logic are shared by both.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite:")


def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    kwargs: dict[str, Any] = {"echo": echo}
    if _is_sqlite(database_url):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 3600

    engine = create_engine(database_url, **kwargs)
    if _is_sqlite(database_url):
        event.listen(engine, "connect", _sqlite_on_connect)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


def init_db(engine: Engine) -> None:
    """Create all tables if they do not exist (idempotent)."""
    # Importing models registers every mapper on Base.metadata.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
