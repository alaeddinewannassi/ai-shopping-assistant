"""Engine/session lifecycle for the tenancy + analytics database (T103).

Deliberately mirrors the "degrade, never crash" posture the rest of the service already
takes with Redis (src/session/store.py) and the CommerceAdapter circuit breaker: if
`DATABASE_URL` is unset or the database is unreachable, `is_configured()` /
`session_scope()` say so and every caller falls back — a chat turn must never fail because
analytics storage is down (Constitution Principle I).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_logger = logging.getLogger("assistant.db")

_lock = threading.Lock()
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_configured_url: str | None = None


def database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    return url or None


def is_configured() -> bool:
    """True when a DATABASE_URL is set. Says nothing about reachability — use
    `check_health()` for that (it backs the /health readiness probe)."""
    return database_url() is not None


def get_engine() -> Engine | None:
    """Lazily builds (and caches) the engine. Returns None when unconfigured.

    Rebuilds if DATABASE_URL changed since the last call, so tests can point the whole
    layer at a throwaway SQLite file without reaching into module internals.
    """
    global _engine, _session_factory, _configured_url

    url = database_url()
    if url is None:
        return None

    with _lock:
        if _engine is not None and _configured_url == url:
            return _engine
        if _engine is not None:
            _engine.dispose()
        _engine = _create_engine(url)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
        _configured_url = url
        return _engine


def _create_engine(url: str) -> Engine:
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        # SQLite is a test-only target; the writer thread (src/analytics/writer.py) and the
        # request threads share one connection pool, so allow cross-thread use.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)
    return sa.create_engine(url, **kwargs)


def get_session() -> Session | None:
    """A new Session, or None when the database isn't configured. Caller owns closing it —
    prefer `session_scope()`."""
    engine = get_engine()
    if engine is None or _session_factory is None:
        return None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session | None]:
    """Transactional scope that yields None (instead of raising) when unconfigured, so call
    sites read as `with session_scope() as db: if db is None: return <fallback>`."""
    session = get_session()
    if session is None:
        yield None
        return
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_health() -> str:
    """`not_configured` | `ok` | `unavailable` — the same three-state vocabulary
    src/api/chat.py:_check_redis() already uses for Redis."""
    if not is_configured():
        return "not_configured"
    try:
        engine = get_engine()
        assert engine is not None
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001 - readiness probes report, never raise
        return "unavailable"


def reset_engine() -> None:
    """Drops the cached engine/session factory. For tests and for config reloads."""
    global _engine, _session_factory, _configured_url
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None
        _configured_url = None
