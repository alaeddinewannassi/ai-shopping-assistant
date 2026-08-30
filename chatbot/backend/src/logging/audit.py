"""Structured JSON audit logging (FR-014, T015) + the Postgres event stream (T303/T304/T310).

Every navigation change, cart mutation, promo suggestion/application, and checkout action
is logged with enough detail to reconstruct the assistant's decisions after the fact.

`log_action()`'s signature and the stdout JSON line it emits are UNCHANGED — every one of
its ~25 existing call sites in agent/dialogue.py needed zero edits for this feature. What's
new: if a `TurnContext` is active (agent/turn_context.py, set by handle_turn() for the
duration of one turn) and the tenancy database is configured, the same event is *also*
queued for durable storage in `tenancy_db`'s `assistant_event` table via a bounded queue +
background writer thread (T304) — so a slow or unreachable database can never add latency
to, or break, a chat turn. The stdout line and the Redis-backed `get_audit_history()` fallback
remain the guarantee that survives even if Postgres is down entirely (Constitution Principle V).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
import uuid
from typing import Any

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover - redis-py is a declared dependency, but keep this
    redis = None  # type: ignore

from datetime import UTC

from src.agent import turn_context

_logger = logging.getLogger("assistant.audit")
if not _logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False

# Per-session audit history, queryable via GET /audit/{session_id} (src/api/chat.py).
# Mirrors SessionStore's Redis-with-in-memory-fallback pattern (src/session/store.py) so
# unit tests and local dev without a running Redis instance still work — history just
# doesn't survive process restarts in that case. This is the fallback path T310 keeps: used
# whenever the tenancy database isn't configured/reachable, or the caller has no tenant_id.
_AUDIT_TTL_SECONDS = 60 * 60  # matches SessionStore.DEFAULT_SESSION_TTL_SECONDS

_redis_client = None
_redis_url = os.environ.get("REDIS_URL")
if redis is not None and _redis_url:
    try:
        _redis_client = redis.from_url(_redis_url, decode_responses=True)
        _redis_client.ping()
    except Exception:  # noqa: BLE001 - fall back to in-memory history below
        _redis_client = None

_memory_history: dict[str, list[dict]] = {}


def _audit_key(session_id: str) -> str:
    return f"audit:{session_id}"


def log_action(
    session_id: str,
    intent: str,
    action: str,
    outcome: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    """Emits one structured JSON audit line.

    Args:
        session_id: which ConversationSession this event belongs to.
        intent: the triggering natural-language intent/action_type (e.g. "propose_add_to_cart").
        action: the concrete action taken (e.g. "add_cart_item(variant=var-tshirt-1-red-m)").
        outcome: "success" | "declined" | "error" | "unavailable" | ... — the result.
        details: any additional structured context (adapter result summary, error message).
    """
    record = {
        "timestamp": time.time(),
        "session_id": session_id,
        "intent": intent,
        "action": action,
        "outcome": outcome,
        "details": details or {},
    }
    _logger.info(json.dumps(record, default=str))
    _persist(session_id, record)
    _enqueue_event(session_id, intent, action, outcome, details or {})


def log_turn_completed(session_id: str) -> None:
    """Emits one extra event marking the end of the current turn, carrying its total
    latency — called once by handle_turn() right before it returns. A no-op (well, still a
    normal log_action-shaped stdout line) outside an active TurnContext, but elapsed_ms is
    only meaningful when one is."""
    turn = turn_context.current()
    elapsed_ms = turn.elapsed_ms if turn is not None else None
    log_action(
        session_id,
        "turn_completed",
        "turn_completed",
        "ok",
        details={"elapsed_ms": elapsed_ms} if elapsed_ms is not None else {},
    )


def _persist(session_id: str, record: dict) -> None:
    if _redis_client is not None:
        try:
            key = _audit_key(session_id)
            _redis_client.rpush(key, json.dumps(record, default=str))
            _redis_client.expire(key, _AUDIT_TTL_SECONDS)
        except Exception:  # noqa: BLE001 - audit persistence must never break a chat turn
            pass
    else:
        _memory_history.setdefault(session_id, []).append(record)


def get_audit_history(session_id: str, *, tenant_id: uuid.UUID | None = None) -> list[dict]:
    """Returns this session's audit trail in chronological order (oldest first), for
    GET /audit/{session_id}. Empty list if the session has no history (unknown, expired,
    or nothing logged yet).

    Reads from the tenancy database (T310) when `tenant_id` is given and it's reachable;
    falls back to the Redis/in-memory path otherwise — same response shape either way, so
    the endpoint's contract is unchanged."""
    if tenant_id is not None:
        rows = _read_events_from_db(tenant_id, session_id)
        if rows is not None:
            return rows

    if _redis_client is not None:
        try:
            raw = _redis_client.lrange(_audit_key(session_id), 0, -1)
            return [json.loads(r) for r in raw]
        except Exception:  # noqa: BLE001 - reads must never raise, just report nothing
            return []
    return list(_memory_history.get(session_id, []))


def _read_events_from_db(tenant_id: uuid.UUID, session_id: str) -> list[dict] | None:
    """None means "couldn't read from the DB, use the fallback" — distinct from an empty
    list, which means "read fine, this session just has no events yet"."""
    from tenancy_db.engine import session_scope
    from tenancy_db.repositories import AssistantEventRepository

    try:
        with session_scope() as db:
            if db is None:
                return None
            events = AssistantEventRepository(db).list_for_session(tenant_id, session_id)
            return [
                {
                    "timestamp": e.occurred_at.timestamp(),
                    "session_id": e.session_id,
                    "intent": e.intent,
                    "action": e.action,
                    "outcome": e.outcome,
                    "details": e.details,
                }
                for e in events
            ]
    except Exception:  # noqa: BLE001 - reads must never raise, just fall back
        return None


# -- Durable event stream: bounded queue + background batch writer (T304) ---------------- #

_MAX_QUEUE_SIZE = 2000
_MAX_BATCH_SIZE = 500
_BATCH_WINDOW_SECONDS = 0.25

_event_queue: queue.Queue[dict] = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
_dropped_events = 0
_dropped_lock = threading.Lock()


def _enqueue_event(
    session_id: str, intent: str, action: str, outcome: str, details: dict[str, Any]
) -> None:
    turn = turn_context.current()
    if turn is None or turn.tenant_id is None:
        return  # no active turn (or a legacy caller with no resolved tenant) — nothing to enrich

    is_turn_completed = intent == "turn_completed"
    row = {
        "event_id": uuid.uuid4(),
        "tenant_id": turn.tenant_id,
        "session_id": session_id,
        "turn_id": turn.turn_id,
        "seq": turn.next_seq(),
        "occurred_at": None,  # server/repository default (utcnow) — never trust client clocks
        "intent": intent,
        "action": action,
        "outcome": outcome,
        "details": details,
        "turn_elapsed_ms": turn.elapsed_ms if is_turn_completed else None,
        # Real LLM usage — only ever set on the turn_completed row of a turn that made an
        # actual model call (FreeTierHostedLLMClient.parse_turn -> TurnContext.record_llm_usage).
        "llm_provider": turn.llm_provider if is_turn_completed else None,
        "llm_model": turn.llm_model if is_turn_completed else None,
        "prompt_tokens": turn.prompt_tokens if is_turn_completed else None,
        "completion_tokens": turn.completion_tokens if is_turn_completed else None,
        "llm_ms": turn.llm_ms if is_turn_completed else None,
        "cost_micros": (0 if turn.llm_provider == "free-tier-hosted" else None) if is_turn_completed else None,
    }
    try:
        _event_queue.put_nowait(row)
    except queue.Full:
        global _dropped_events
        with _dropped_lock:
            _dropped_events += 1


def dropped_event_count() -> int:
    """Test/ops helper: how many events have been dropped since process start because the
    queue was full (the database was down or falling behind)."""
    with _dropped_lock:
        return _dropped_events


def _flush_batch(rows: list[dict]) -> None:
    from datetime import datetime

    from tenancy_db.engine import session_scope
    from tenancy_db.repositories import AssistantEventRepository

    for row in rows:
        if row["occurred_at"] is None:
            row["occurred_at"] = datetime.now(UTC)

    try:
        with session_scope() as db:
            if db is None:
                return  # DB not configured/reachable — best-effort, drop this batch silently
            AssistantEventRepository(db).insert_many(rows)
    except Exception:  # noqa: BLE001 - the writer thread must never die or raise
        pass


def _writer_loop() -> None:
    while True:
        row = _event_queue.get()
        batch = [row]
        deadline = time.monotonic() + _BATCH_WINDOW_SECONDS
        while len(batch) < _MAX_BATCH_SIZE:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(_event_queue.get(timeout=remaining))
            except queue.Empty:
                break
        _flush_batch(batch)
        for _ in batch:
            _event_queue.task_done()


_writer_thread = threading.Thread(target=_writer_loop, name="assistant-event-writer", daemon=True)
_writer_thread.start()


def wait_for_drain(timeout: float = 2.0) -> None:
    """Test-only: blocks until every queued event has been flushed (or `timeout` elapses).
    Not used by any production code path — a chat turn never waits on this."""
    deadline = time.monotonic() + timeout
    while _event_queue.unfinished_tasks > 0 and time.monotonic() < deadline:
        time.sleep(0.005)
