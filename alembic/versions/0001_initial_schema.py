"""Initial CloudDisk schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-20

``sql/schema.sql`` is the human-readable MySQL 8 source of truth.  This
initial revision intentionally mirrors the SQLAlchemy metadata so that
``alembic upgrade head`` works on any supported database (MySQL or SQLite).
Later schema changes should be added as ordinary, explicit Alembic
revisions (``alembic revision --autogenerate``).
"""

from __future__ import annotations

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from shared.db.models import Base

    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    from shared.db.models import Base

    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
