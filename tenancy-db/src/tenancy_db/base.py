"""Declarative base + portable column types (T103).

The production target is PostgreSQL, but the test suite runs against SQLite so the whole
Phase 1/2/3 surface stays testable with no external service (mirroring the existing
Redis-optional design in src/session/store.py). Everything here therefore either uses a
type SQLAlchemy already renders per-dialect (`sa.Uuid`) or declares an explicit
`.with_variant()` for PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Explicit constraint naming so Alembic autogenerate can always emit a DROP for them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB on PostgreSQL (indexable, typed); plain JSON elsewhere so SQLite-backed tests work.
JSONType = sa.JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """Timezone-aware UTC now. Used as a Python-side default so SQLite and PostgreSQL agree
    (SQLite has no native timestamptz)."""
    return datetime.now(UTC)


def uuid_pk():
    """Standard UUID primary key column."""
    return mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)


def created_at_column():
    return mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)


def updated_at_column():
    return mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
