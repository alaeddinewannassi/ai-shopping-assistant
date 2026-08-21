"""Unit tests for the pending-action state machine (T017).

These tests assert the structural guarantee behind Constitution Principle III: no mutation
reachable without an explicit, matching confirm(); staleness/topic-change invalidates a
pending action rather than letting a stray later "yes" confirm it (research.md §9.3, §9.4).
"""

from __future__ import annotations

import pytest

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
