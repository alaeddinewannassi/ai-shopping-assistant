"""Structured JSON audit logging (FR-014, T015).

Every navigation change, cart mutation, promo suggestion/application, and checkout action
is logged with enough detail to reconstruct the assistant's decisions after the fact.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Optional

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover - redis-py is a declared dependency, but keep this
    redis = None  # type: ignore

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
# doesn't survive process restarts in that case.
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
    details: Optional[dict[str, Any]] = None,
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


def get_audit_history(session_id: str) -> list[dict]:
    """Returns this session's audit trail in chronological order (oldest first), for
    GET /audit/{session_id}. Empty list if the session has no history (unknown, expired,
    or nothing logged yet)."""
    if _redis_client is not None:
        try:
            raw = _redis_client.lrange(_audit_key(session_id), 0, -1)
            return [json.loads(r) for r in raw]
        except Exception:  # noqa: BLE001 - reads must never raise, just report nothing
            return []
    return list(_memory_history.get(session_id, []))
