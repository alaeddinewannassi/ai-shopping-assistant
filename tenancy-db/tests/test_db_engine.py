"""src/db/engine.py: DATABASE_URL-unset/unreachable must degrade, never raise (T103).

Mirrors src/session/store.py's Redis-optional posture (Constitution Principle I — a chat
turn, and by extension every health/readiness check, must never fail because analytics
storage is down or simply not configured).
"""

from __future__ import annotations

import sqlalchemy as sa

from tenancy_db import engine as db_engine


def test_not_configured_when_database_url_unset(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_engine.reset_engine()
    assert db_engine.is_configured() is False
    assert db_engine.get_engine() is None
    assert db_engine.check_health() == "not_configured"

    with db_engine.session_scope() as session:
        assert session is None


def test_ok_against_a_reachable_sqlite_database(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "engine_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    db_engine.reset_engine()
    try:
        assert db_engine.is_configured() is True
        assert db_engine.check_health() == "ok"

        with db_engine.session_scope() as session:
            assert session is not None
            session.execute(sa.text("SELECT 1"))
    finally:
        db_engine.reset_engine()


def test_unavailable_for_an_unreachable_database(monkeypatch) -> None:
    # A syntactically valid but unreachable Postgres URL — connection fails at query time,
    # not at engine construction (SQLAlchemy engines are lazy).
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@127.0.0.1:1/nonexistent")
    db_engine.reset_engine()
    try:
        assert db_engine.is_configured() is True
        assert db_engine.check_health() == "unavailable"
    finally:
        db_engine.reset_engine()
