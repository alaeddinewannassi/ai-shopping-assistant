"""Unit tests for the pending-action state machine (T017).

These tests assert the structural guarantee behind Constitution Principle III: no mutation
reachable without an explicit, matching confirm(); staleness/topic-change invalidates a
pending action rather than letting a stray later "yes" confirm it (research.md §9.3, §9.4).
"""

from __future__ import annotations

import pytest

from src.adapters.base import CartStateChangedError, PromoInvalidError
from src.adapters.mock import MockAdapter
from src.agent.pending import MUTATING_ACTION_TYPES, PendingActionError, PendingActionGate
from src.session.store import SessionStore


@pytest.fixture
def gate() -> PendingActionGate:
    return PendingActionGate(SessionStore(redis_url=None), MockAdapter())


def test_propose_does_not_mutate_cart(gate: PendingActionGate) -> None:
    gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add 1x Classic T-Shirt (Red, M) — $19.99?",
    )
    cart = gate._adapter.get_cart("s1")
    assert cart.lines == [], "propose() must never call the adapter"


def test_confirm_with_matching_action_id_executes_mutation(gate: PendingActionGate) -> None:
    action = gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add 1x Classic T-Shirt (Red, M) — $19.99?",
    )
    result = gate.confirm("s1", action.action_id)
    assert result.cart is not None
    assert len(result.cart.lines) == 1


def test_confirm_with_wrong_action_id_raises_and_does_not_mutate(gate: PendingActionGate) -> None:
    gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add 1x Classic T-Shirt (Red, M) — $19.99?",
    )
    with pytest.raises(PendingActionError):
        gate.confirm("s1", "not-the-real-action-id")
    cart = gate._adapter.get_cart("s1")
    assert cart.lines == []


def test_confirm_with_no_pending_action_raises(gate: PendingActionGate) -> None:
    with pytest.raises(PendingActionError):
        gate.confirm("s1", "some-action-id")


def test_decline_clears_pending_action_so_later_confirm_fails(gate: PendingActionGate) -> None:
    action = gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add 1x Classic T-Shirt (Red, M) — $19.99?",
    )
    gate.decline("s1")
    with pytest.raises(PendingActionError):
        gate.confirm("s1", action.action_id)


def test_new_proposal_invalidates_prior_pending_action(gate: PendingActionGate) -> None:
    """research.md §9.4: moving on to a different product/variant/quantity invalidates the
    old PendingAction — a stray later 'yes' can't confirm a stale, no-longer-relevant one."""
    first = gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add the red t-shirt?",
    )
    gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-blue-m", "quantity": 1},
        recap_text="Add the blue t-shirt instead?",
    )
    with pytest.raises(PendingActionError):
        gate.confirm("s1", first.action_id)


def test_confirm_clears_pending_action_after_execution_no_double_confirm(
    gate: PendingActionGate,
) -> None:
    action = gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add 1x Classic T-Shirt (Red, M) — $19.99?",
    )
    gate.confirm("s1", action.action_id)
    with pytest.raises(PendingActionError):
        gate.confirm("s1", action.action_id)


def test_propose_rejects_unknown_action_type(gate: PendingActionGate) -> None:
    with pytest.raises(ValueError):
        gate.propose("s1", "not_a_real_action", {}, recap_text="?")


def _age_pending_action(gate: PendingActionGate, session_id: str, seconds: float) -> None:
    session = gate._sessions.get_or_create(session_id)
    assert session.pending_action is not None
    session.pending_action.created_at -= seconds
    gate._sessions.save(session)


def test_confirm_rejects_a_stale_non_checkout_action_without_executing_it(
    gate: PendingActionGate,
) -> None:
    """Regression test for a real gap found via adversarial review: is_stale() (FR-009/US3
    Scenario 4, a 300s staleness window) existed but had zero call sites anywhere — a
    shopper could approve a recap arbitrarily long after it was shown (up to the full
    session TTL) with no re-check at all. A stale non-checkout confirmation must be
    discarded exactly like an already-invalidated one, never executed."""
    action = gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add 1x Classic T-Shirt (Red, M) — $19.99?",
    )
    _age_pending_action(gate, "s1", seconds=301)

    with pytest.raises(PendingActionError):
        gate.confirm("s1", action.action_id)

    cart = gate._adapter.get_cart("s1")
    assert cart.lines == [], "a stale confirmation must never execute the mutation"
    # Spent either way (matches the finally-block guarantee for every other confirm() path).
    with pytest.raises(PendingActionError):
        gate.confirm("s1", action.action_id)


def test_confirm_re_prompts_for_a_fresh_recap_on_a_stale_checkout(
    gate: PendingActionGate,
) -> None:
    """checkout gets the SAME re-validate-and-re-propose treatment as a genuine cart-state
    change (CartStateChangedError) rather than the generic "ask again" — that flow already
    exists, is already tested (dialogue.py's _handle_checkout_state_changed), and correctly
    shows a fresh, live recap instead of just discarding the shopper's stated intent."""
    proposal = gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add 1x Classic T-Shirt (Red, M) — $19.99?",
    )
    add_result = gate.confirm("s1", proposal.action_id)
    assert add_result.cart is not None

    checkout_action = gate.propose("s1", "checkout", {}, recap_text="Place your order for $19.99?")
    _age_pending_action(gate, "s1", seconds=301)

    with pytest.raises(CartStateChangedError):
        gate.confirm("s1", checkout_action.action_id)


def test_confirm_executes_normally_within_the_staleness_window(gate: PendingActionGate) -> None:
    """A confirmation well within the window must not be treated as stale."""
    action = gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add 1x Classic T-Shirt (Red, M) — $19.99?",
    )
    _age_pending_action(gate, "s1", seconds=5)

    result = gate.confirm("s1", action.action_id)
    assert result.cart is not None
    assert len(result.cart.lines) == 1


# -- Client-cart-sync (specs/003-adversarial-qa-review) ------------------------------- #
#
# Regression coverage for a real bug found via adversarial review: the chatbot's own
# webservice-created cart and the shopper's real front-end session cart were completely
# disconnected. For a session that has provided a real client_cart_snapshot, confirm() must
# never call the adapter's own add/update/remove/checkout — those would mutate a cart the
# storefront never sees — and must instead return an instruction for the widget to execute.


class _ClientSyncAdapter(MockAdapter):
    """A MockAdapter that also declares client-cart-sync support, and raises if any of the
    mutating methods it should now be BYPASSING for a synced session are ever called."""

    supports_client_cart_sync = True

    def cart_from_snapshot(self, snapshot):
        from src.adapters.base import Cart, CartLine

        lines = [
            CartLine(product_id=row["variant_id"].split("#")[0], variant_id=row["variant_id"], quantity=row["quantity"], unit_price=1.0)
            for row in snapshot
        ]
        return Cart(id="client-cart", lines=lines)

    def add_cart_item(self, *a, **k):
        raise AssertionError("must not call the adapter directly for a client-synced session")

    def update_cart_item(self, *a, **k):
        raise AssertionError("must not call the adapter directly for a client-synced session")

    def remove_cart_item(self, *a, **k):
        raise AssertionError("must not call the adapter directly for a client-synced session")

    def apply_promo(self, *a, **k):
        raise AssertionError("must not call the adapter directly for a client-synced session")

    def checkout(self, *a, **k):
        raise AssertionError("must not call the adapter directly for a client-synced session")


@pytest.fixture
def synced_gate() -> PendingActionGate:
    return PendingActionGate(SessionStore(redis_url=None), _ClientSyncAdapter())


def _give_snapshot(gate: PendingActionGate, session_id: str, snapshot: list[dict]) -> None:
    session = gate._sessions.get_or_create(session_id)
    session.client_cart_snapshot = snapshot
    gate._sessions.save(session)


def test_confirmed_add_returns_a_client_cart_action_instead_of_mutating_the_adapter(
    synced_gate: PendingActionGate,
) -> None:
    _give_snapshot(synced_gate, "s1", [])
    action = synced_gate.propose(
        "s1", "add_cart_item",
        {"product_id": "18", "variant_id": "18#36", "quantity": 2},
        recap_text="Add 2x Notebook?",
    )
    result = synced_gate.confirm("s1", action.action_id)

    assert result.client_cart_action == {"op": "increment", "variant_id": "18#36", "quantity": 2}
    assert result.cart is not None
    assert result.cart.lines[0].quantity == 2  # predicted, from the (empty) pre-mutation snapshot


def test_confirmed_update_reports_the_absolute_target_quantity(synced_gate: PendingActionGate) -> None:
    _give_snapshot(synced_gate, "s1", [{"variant_id": "18#36", "quantity": 2}])
    action = synced_gate.propose(
        "s1", "update_cart_item", {"variant_id": "18#36", "quantity": 5}, recap_text="Set to 5?"
    )
    result = synced_gate.confirm("s1", action.action_id)

    assert result.client_cart_action == {"op": "set", "variant_id": "18#36", "quantity": 5}
    assert result.cart.lines[0].quantity == 5


def test_confirmed_remove_needs_no_quantity(synced_gate: PendingActionGate) -> None:
    _give_snapshot(synced_gate, "s1", [{"variant_id": "18#36", "quantity": 2}])
    action = synced_gate.propose("s1", "remove_cart_item", {"variant_id": "18#36"}, recap_text="Remove it?")
    result = synced_gate.confirm("s1", action.action_id)

    assert result.client_cart_action == {"op": "remove", "variant_id": "18#36"}
    assert result.cart.lines == []


def test_confirmed_promo_applies_via_the_real_front_office_discount_endpoint(
    synced_gate: PendingActionGate,
) -> None:
    """Real PrestaShop CartController.php supports addDiscount the same way it supports
    add/update/delete — applying a promo for a synced session is a genuine write to the
    shopper's own real cart, not a write to the backend's disconnected one (which the
    _ClientSyncAdapter.apply_promo AssertionError below would catch if this regressed)."""
    _give_snapshot(synced_gate, "s1", [{"variant_id": "18#36", "quantity": 2}])
    action = synced_gate.propose("s1", "apply_promo", {"code": "WELCOME10"}, recap_text="Apply WELCOME10?")
    result = synced_gate.confirm("s1", action.action_id)

    assert result.client_cart_action == {"op": "apply_promo", "code": "WELCOME10"}
    assert result.cart is not None
    assert result.cart.applied_promo_code == "WELCOME10"
    assert result.cart.discount_total > 0


def test_confirmed_promo_reports_honest_reason_when_no_longer_valid(synced_gate: PendingActionGate) -> None:
    """Re-validated fresh at confirm time, not just trusted from proposal time — a code that
    was valid when suggested but isn't anymore (or was never real) must never be reported as
    applied."""
    _give_snapshot(synced_gate, "s1", [{"variant_id": "18#36", "quantity": 2}])
    action = synced_gate.propose("s1", "apply_promo", {"code": "FAKE99"}, recap_text="Apply FAKE99?")
    with pytest.raises(PromoInvalidError):
        synced_gate.confirm("s1", action.action_id)


def test_confirmed_checkout_hands_off_instead_of_placing_an_order(synced_gate: PendingActionGate) -> None:
    _give_snapshot(synced_gate, "s1", [{"variant_id": "18#36", "quantity": 2}])
    action = synced_gate.propose("s1", "checkout", {}, recap_text="Place your order?")
    result = synced_gate.confirm("s1", action.action_id)

    assert result.handoff_to_native_checkout is True
    assert result.order is None


def test_a_session_with_no_snapshot_still_uses_the_adapter_directly_even_on_a_sync_capable_adapter() -> None:
    """A non-browser API caller (no window.prestashop, nothing to snapshot) must keep
    getting today's backend-owned-cart behavior — client-sync is per-SESSION (has this
    session actually provided a snapshot?), never just "is the adapter capable of it"."""
    adapter = MockAdapter()

    class _CapableButNoSnapshotAdapter(_ClientSyncAdapter):
        def add_cart_item(self, cart_id, product_id, variant_id, quantity):
            return adapter.add_cart_item(cart_id, product_id, variant_id, quantity)

    gate = PendingActionGate(SessionStore(redis_url=None), _CapableButNoSnapshotAdapter())
    # Deliberately never calling _give_snapshot — session.client_cart_snapshot stays None.
    action = gate.propose(
        "s1",
        "add_cart_item",
        {"product_id": "prod-tshirt-1", "variant_id": "var-tshirt-1-red-m", "quantity": 1},
        recap_text="Add 1x Classic T-Shirt?",
    )
    result = gate.confirm("s1", action.action_id)

    assert result.client_cart_action is None
    assert result.cart is not None
    assert len(result.cart.lines) == 1


def test_all_mutating_action_types_are_reachable_only_through_gate() -> None:
    """Documents the capability boundary (research.md §9.3): this is the exhaustive list of
    mutation types, and only PendingActionGate.confirm() may execute them."""
    assert MUTATING_ACTION_TYPES == {
        "add_cart_item",
        "update_cart_item",
        "remove_cart_item",
        "apply_promo",
        "checkout",
    }
