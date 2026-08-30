"""Sets the Postgres session GUC RLS policies check against (T106).

`session_scope()` (src/db/engine.py) gives callers a plain SQLAlchemy Session; this module
is the one place that stamps `app.current_tenant_id` onto it so the RLS policies from the
tenancy-baseline migration actually scope every query. It's a defense-in-depth backstop —
every repository method below also filters by `tenant_id` explicitly, so isolation holds
even on SQLite (no RLS support) where this is a no-op.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy.orm import Session


@contextmanager
def scoped_to_tenant(session: Session, tenant_id: uuid.UUID) -> Iterator[None]:
    """Binds `tenant_id` to the current transaction via `SET LOCAL` (Postgres only — a
    no-op, harmlessly, on SQLite). Must be called with an already-open transaction; scope
    ends at the next commit/rollback, matching `session_scope()`'s lifetime."""
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            sa.text("SET LOCAL app.current_tenant_id = :tid"), {"tid": str(tenant_id)}
        )
    yield
