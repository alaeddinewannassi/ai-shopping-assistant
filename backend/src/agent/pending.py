"""Pending-action state machine (research.md §4, §9.3; Constitution Principle III).

This module is the ONLY code path allowed to invoke a mutating CommerceAdapter method
(add_cart_item, update_cart_item, remove_cart_item, apply_promo, checkout). It is not an
LLM-callable tool — the LLM's tool schema (agent/intents.py) only ever exposes
`propose_action`, never these adapter methods directly (research.md §9.3, closing the
prompt-injection gap: there is no tool in the LLM's schema that could execute a mutation,
so no phrasing of a user message can make one happen without going through `confirm()`
below, which independently re-checks that a matching, unexpired PendingAction exists).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.adapters.base import Cart, CommerceAdapter
from src.session.store import PendingAction, SessionStore

# Mutation types this state machine gates. Every one of these MUST have gone through
# propose() -> an explicit confirm() before the corresponding adapter method is called.
MUTATING_ACTION_TYPES = {
    "add_cart_item",
    "update_cart_item",
    "remove_cart_item",
    "apply_promo",
    "checkout",
}


@dataclass
class ActionResult:
    action_type: str
    cart: Optional[Cart] = None
    error: Optional[str] = None


class PendingActionError(Exception):
    """Raised when confirm()/decline() is attempted against a missing/stale/mismatched
    PendingAction — the caller (dialogue layer) must treat this as "nothing to confirm",
    never as an implicit approval of some other action (research.md §9.4)."""


class PendingActionGate:
    """Wraps a SessionStore + CommerceAdapter to enforce confirm-before-mutate."""

    def __init__(self, session_store: SessionStore, adapter: CommerceAdapter) -> None:
        self._sessions = session_store
        self._adapter = adapter

    def propose(
        self, session_id: str, action_type: str, parameters: dict, recap_text: str
    ) -> PendingAction:
        """Records a proposed mutation with its human-readable recap. Does NOT call the
        adapter — no mutation happens until an explicit confirm() with a matching action_id.
        """
        if action_type not in MUTATING_ACTION_TYPES:
            raise ValueError(f"Not a recognized mutating action type: {action_type}")
        return self._sessions.propose_action(session_id, action_type, parameters, recap_text)

    def decline(self, session_id: str) -> None:
        """Shopper declined, or changed topic — the pending action is discarded, never
        executed (research.md §9.4)."""
        self._sessions.clear_pending_action(session_id)

    def confirm(self, session_id: str, action_id: str) -> ActionResult:
        """The single choke point: executes the adapter mutation call IFF a PendingAction
        with matching action_id exists and can be marked confirmed.

        Raises PendingActionError if there is no matching pending action (e.g. the shopper
        said "yes" to something stale/already-cleared, or is trying to confirm an action_id
        that was never actually proposed) — this is what makes a bare "yes"/prompt-injection
        attempt structurally incapable of triggering a mutation (research.md §9.3).
        """
        action = self._sessions.confirm_action(session_id, action_id)
        if action is None:
            raise PendingActionError(
                "No matching pending action to confirm (it may have expired, been "
                "invalidated by a topic change, or never existed)."
            )

        try:
            cart = self._execute(session_id, action)
            return ActionResult(action_type=action.action_type, cart=cart)
        finally:
            # Whether it succeeded or raised, this PendingAction is spent — clear it so a
            # later stray "yes" can't re-trigger or retry it silently.
            self._sessions.clear_pending_action(session_id)

    def _execute(self, session_id: str, action: PendingAction) -> Optional[Cart]:
        params = action.parameters
        session = self._sessions.get_or_create(session_id)
        cart_id = session.cart_id or session_id

        if action.action_type == "add_cart_item":
            return self._adapter.add_cart_item(
                cart_id, params["product_id"], params["variant_id"], params["quantity"]
            )
        if action.action_type == "update_cart_item":
            return self._adapter.update_cart_item(cart_id, params["variant_id"], params["quantity"])
        if action.action_type == "remove_cart_item":
            return self._adapter.remove_cart_item(cart_id, params["variant_id"])
        if action.action_type == "apply_promo":
            return self._adapter.apply_promo(cart_id, params["code"])
        if action.action_type == "checkout":
            self._adapter.checkout(cart_id)
            return None

        raise ValueError(f"Unhandled mutating action type: {action.action_type}")

    def is_stale(self, action: PendingAction, *, max_age_seconds: float = 300.0) -> bool:
        """Staleness check (FR-009 / US3 Scenario 4): callers should re-validate cart/stock/
        price and create a fresh PendingAction (with updated recap) rather than confirming
        an old one past this age."""
        import time

        return (time.time() - action.created_at) > max_age_seconds
