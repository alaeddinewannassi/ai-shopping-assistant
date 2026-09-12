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

from src.adapters.base import Cart, CartStateChangedError, CommerceAdapter, Order
from src.session.store import ConversationSession, PendingAction, SessionStore

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
    order: Optional[Order] = None
    error: Optional[str] = None
    # Populated instead of actually calling the adapter's mutating method, ONLY for a
    # session that has already provided a real client_cart_snapshot (see that field's
    # docstring on ConversationSession) — the widget executes this against the store's real
    # front-office cart, since that's the only place with legitimate access to the shopper's
    # actual session. `cart` above is still populated in this case too, as a best-effort
    # PREDICTED post-mutation cart (computed from the pre-mutation snapshot the exact same
    # way the widget's own write will), so the recap text has something accurate to show
    # immediately rather than waiting on a round trip that hasn't happened yet.
    client_cart_action: Optional[dict] = None
    # Set instead of executing checkout when the session's cart lives in the shopper's real
    # PrestaShop session (client-synced) rather than a webservice-created cart this backend
    # can place a real order against — see confirm()'s checkout branch.
    handoff_to_native_checkout: bool = False


def _apply_client_cart_op(snapshot: list[dict], op: str, variant_id: str, quantity: int) -> list[dict]:
    """Pure function: what the snapshot would look like after `op` — used both to predict
    the post-mutation Cart for the recap, and mirrors exactly what the widget's own
    front-office AJAX call does (increment/set/remove), so the two never disagree."""
    rows = [dict(row) for row in snapshot if str(row.get("variant_id")) != variant_id]
    if op == "remove":
        return rows
    current = next(
        (row for row in snapshot if str(row.get("variant_id")) == variant_id), None
    )
    if op == "increment":
        new_quantity = (int(current["quantity"]) if current else 0) + quantity
    else:  # "set"
        new_quantity = quantity
    if new_quantity > 0:
        rows.append({"variant_id": variant_id, "quantity": new_quantity})
    return rows


class PendingActionError(Exception):
    """Raised when confirm()/decline() is attempted against a missing/stale/mismatched
    PendingAction — the caller (dialogue layer) must treat this as "nothing to confirm",
    never as an implicit approval of some other action (research.md §9.4)."""


class PromoNotSyncableError(Exception):
    """Raised instead of applying a promo code for a client-cart-synced session — see
    confirm()'s apply_promo branch. The dialogue layer turns this into an honest reply
    pointing at the store's own native checkout discount field."""


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

        session = self._sessions.get_or_create(session_id)
        client_synced = session.client_cart_snapshot is not None and getattr(
            self._adapter, "supports_client_cart_sync", False
        )

        try:
            if self.is_stale(action):
                # FR-009/US3 Scenario 4, generalized (real, confirmed gap from adversarial
                # review): is_stale() existed with a documented 300s window but had zero
                # call sites anywhere — a shopper could approve a recap up to the full 1-hour
                # session TTL later with no re-check at all. checkout already has a tested,
                # correct re-validate-and-re-propose flow for exactly this
                # (CartStateChangedError -> _handle_checkout_state_changed re-fetches the
                # cart and shows a fresh recap) — reuse it here rather than executing a stale
                # approval. Every other mutation type is safer to simply discard (existing
                # PendingActionError handling: "that confirmation isn't valid anymore, could
                # you tell me again?") than to guess at re-validating and re-recapping each
                # of add/update/remove/promo individually.
                if action.action_type == "checkout":
                    raise CartStateChangedError(
                        "Pending checkout confirmation is stale; a fresh recap is required."
                    )
                raise PendingActionError(
                    "Pending action expired (stale confirmation window) — ask again."
                )
            if action.action_type == "checkout":
                if client_synced:
                    # The backend has no cart to place a real order against — the shopper's
                    # actual cart lives in their own PrestaShop session (see
                    # ConversationSession.client_cart_snapshot's docstring). Hand off to
                    # PrestaShop's own real checkout flow instead of silently placing an
                    # order for whatever the backend's disconnected webservice cart happens
                    # to contain (most likely empty, since nothing has written to it).
                    return ActionResult(action_type=action.action_type, handoff_to_native_checkout=True)
                order = self._adapter.checkout(self._cart_id_for(session_id))
                # The cart just placed as an order no longer represents "the shopper's
                # current cart" — clear the persisted id so the next add/get starts a fresh
                # one, matching PrestaShopAdapter.checkout()'s own _cart_id_map cleanup.
                fresh_session = self._sessions.get_or_create(session_id)
                if fresh_session.cart_id is not None:
                    fresh_session.cart_id = None
                    self._sessions.save(fresh_session)
                return ActionResult(action_type=action.action_type, order=order)
            if action.action_type == "apply_promo" and client_synced:
                # Same reasoning as checkout above: applying a promo writes a cart-scoped
                # discount to the backend's OWN (disconnected, likely-empty) cart, which
                # would silently do nothing for the shopper's real order. Honest decline
                # rather than a false "applied!" — PrestaShop's real checkout has its own
                # native discount-code field.
                raise PromoNotSyncableError(
                    "Cannot apply a promo code to a client-synced cart from chat; the "
                    "shopper's real checkout page has its own discount-code field."
                )
            cart, client_cart_action = self._execute(session_id, action, session, client_synced)
            if cart is not None and not client_synced:
                self._sessions.remember_cart_id(self._sessions.get_or_create(session_id), cart.id)
            return ActionResult(
                action_type=action.action_type, cart=cart, client_cart_action=client_cart_action
            )
        finally:
            # Whether it succeeded or raised, this PendingAction is spent — clear it so a
            # later stray "yes" can't re-trigger or retry it silently.
            self._sessions.clear_pending_action(session_id)

    def _cart_id_for(self, session_id: str) -> str:
        session = self._sessions.get_or_create(session_id)
        return session.cart_id or session_id

    def _execute(
        self, session_id: str, action: PendingAction, session: ConversationSession, client_synced: bool
    ) -> tuple[Optional[Cart], Optional[dict]]:
        params = action.parameters

        if client_synced:
            op = {
                "add_cart_item": "increment",
                "update_cart_item": "set",
                "remove_cart_item": "remove",
            }[action.action_type]
            variant_id = params["variant_id"]
            quantity = params.get("quantity", 0)
            snapshot = session.client_cart_snapshot or []
            predicted_snapshot = _apply_client_cart_op(snapshot, op, variant_id, quantity)
            predicted_cart = self._adapter.cart_from_snapshot(predicted_snapshot)
            client_cart_action: dict = {"op": op, "variant_id": variant_id}
            if op != "remove":
                client_cart_action["quantity"] = quantity
            return predicted_cart, client_cart_action

        cart_id = self._cart_id_for(session_id)
        if action.action_type == "add_cart_item":
            return (
                self._adapter.add_cart_item(
                    cart_id, params["product_id"], params["variant_id"], params["quantity"]
                ),
                None,
            )
        if action.action_type == "update_cart_item":
            return self._adapter.update_cart_item(cart_id, params["variant_id"], params["quantity"]), None
        if action.action_type == "remove_cart_item":
            return self._adapter.remove_cart_item(cart_id, params["variant_id"]), None
        if action.action_type == "apply_promo":
            return self._adapter.apply_promo(cart_id, params["code"]), None

        raise ValueError(f"Unhandled mutating action type: {action.action_type}")

    def is_stale(self, action: PendingAction, *, max_age_seconds: float = 300.0) -> bool:
        """Staleness check (FR-009 / US3 Scenario 4): callers should re-validate cart/stock/
        price and create a fresh PendingAction (with updated recap) rather than confirming
        an old one past this age."""
        import time

        return (time.time() - action.created_at) > max_age_seconds
