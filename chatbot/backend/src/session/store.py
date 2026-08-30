"""Redis-backed ConversationSession + PendingAction store (data-model.md, T013).

Falls back to an in-process in-memory store when Redis isn't reachable, so unit tests and
local dev don't hard-require a running Redis instance — but production/demo use should set
REDIS_URL (backend/.env.example) for real multi-process persistence.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover - redis-py is a declared dependency, but keep this
    redis = None  # type: ignore


@dataclass
class PendingAction:
    """The structural confirm-before-mutate gate (Constitution Principle III).

    See research.md §9.3/§9.4: only `confirm_action()` in agent/pending.py may turn this
    into a real adapter mutation call; the LLM itself never has a tool that can do so.
    """

    action_id: str
    action_type: str  # add_cart_item | update_cart_item | remove_cart_item | apply_promo | checkout
    parameters: dict
    recap_text: str
    created_at: float
    confirmed: bool = False


@dataclass
class ConversationSession:
    session_id: str
    cart_id: str | None = None
    navigation_context: dict = field(default_factory=dict)
    # A compact text summary of the products from the most recent search/navigate result
    # (dialogue.py's _format_products — "Name ($price); ..."), fed back to the LLM as context
    # on the NEXT turn. Without this, a follow-up question like "does it fit a young man?"
    # right after a search result has zero connection to what was just shown — the LLM has
    # no way to know what "it" refers to, even though the shopper can see it right above.
    last_shown_products: str = ""
    pending_action: PendingAction | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # US4/data-model.md PromoStrategy `first_order` signal: this shopper session has not
    # yet completed a checkout. Flipped once by dialogue.py after a successful order — this
    # feature has no account/login system, so "first order" is scoped to this session.
    has_completed_order: bool = False


class SessionStore:
    """Get/create sessions, read/write the one in-flight PendingAction per session."""

    DEFAULT_SESSION_TTL_SECONDS = 60 * 60  # 1 hour of shopper inactivity

    def __init__(self, redis_url: str | None = None, *, key_prefix: str = "") -> None:
        # key_prefix namespaces Redis keys per tenant (T204) — empty for the legacy
        # single-tenant/default deployment so its existing keys are unaffected.
        self._key_prefix = key_prefix
        self._redis_url = redis_url or os.environ.get("REDIS_URL")
        self._client = None
        if redis is not None and self._redis_url:
            try:
                self._client = redis.from_url(self._redis_url, decode_responses=True)
                self._client.ping()
            except Exception:  # noqa: BLE001 - fall back to in-memory store below
                self._client = None
        self._memory: dict[str, ConversationSession] = {}

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}session:{session_id}"

    def get_or_create(self, session_id: str) -> ConversationSession:
        existing = self._read(session_id)
        if existing is not None:
            return existing
        session = ConversationSession(session_id=session_id)
        self._write(session)
        return session

    def save(self, session: ConversationSession) -> None:
        session.updated_at = time.time()
        self._write(session)

    def propose_action(
        self, session_id: str, action_type: str, parameters: dict, recap_text: str
    ) -> PendingAction:
        """Creates a new PendingAction, replacing/invalidating any prior one (research.md §9.4:
        a stray later 'yes' must never confirm a stale, no-longer-relevant proposal)."""
        session = self.get_or_create(session_id)
        action = PendingAction(
            action_id=str(uuid.uuid4()),
            action_type=action_type,
            parameters=parameters,
            recap_text=recap_text,
            created_at=time.time(),
            confirmed=False,
        )
        session.pending_action = action
        self.save(session)
        return action

    def confirm_action(self, session_id: str, action_id: str) -> PendingAction | None:
        """Marks the pending action confirmed IFF it matches action_id exactly — returns
        None if there is no matching pending action (e.g. it was invalidated/expired)."""
        session = self.get_or_create(session_id)
        action = session.pending_action
        if action is None or action.action_id != action_id:
            return None
        action.confirmed = True
        self.save(session)
        return action

    def clear_pending_action(self, session_id: str) -> None:
        """Invalidates the current pending action (decline, topic change, or post-execution)."""
        session = self.get_or_create(session_id)
        session.pending_action = None
        self.save(session)

    def _read(self, session_id: str) -> ConversationSession | None:
        if self._client is not None:
            raw = self._client.get(self._key(session_id))
            if raw is None:
                return None
            data = json.loads(raw)
            pending = data.get("pending_action")
            return ConversationSession(
                session_id=data["session_id"],
                cart_id=data.get("cart_id"),
                navigation_context=data.get("navigation_context", {}),
                last_shown_products=data.get("last_shown_products", ""),
                pending_action=PendingAction(**pending) if pending else None,
                created_at=data.get("created_at", time.time()),
                updated_at=data.get("updated_at", time.time()),
                has_completed_order=data.get("has_completed_order", False),
            )
        return self._memory.get(session_id)

    def _write(self, session: ConversationSession) -> None:
        if self._client is not None:
            payload = asdict(session)
            self._client.set(
                self._key(session.session_id),
                json.dumps(payload),
                ex=self.DEFAULT_SESSION_TTL_SECONDS,
            )
        else:
            self._memory[session.session_id] = session
