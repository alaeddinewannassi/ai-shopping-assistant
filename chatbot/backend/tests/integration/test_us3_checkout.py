"""Integration tests for User Story 3 - Checkout with Full Recap & Final Confirmation
(T039-T043).

Exercises the request_checkout -> confirm/decline flow through agent/dialogue.py's
DialogueContext, the same wiring api/chat.py uses, backed by MockAdapter + PendingActionGate.
"""

from __future__ import annotations

import copy

import pytest

from src.adapters.mock import MockAdapter
from src.agent.dialogue import DialogueContext, handle_turn
from src.agent.intents import CartIntentHandler, DiscoveryIntentHandler
from src.agent.llm_client import RuleBasedStubClient
from src.agent.pending import PendingActionGate
from src.agent.taxonomy_resolver import TaxonomyResolver
from src.session.catalog_cache import CatalogSnapshotCache
from src.session.store import SessionStore


@pytest.fixture
def adapter() -> MockAdapter:
    return MockAdapter()


@pytest.fixture
def llm_client() -> RuleBasedStubClient:
    return RuleBasedStubClient()


@pytest.fixture
def session_store() -> SessionStore:
    return SessionStore(redis_url=None)


def _ctx(adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore) -> DialogueContext:
    resolver = TaxonomyResolver(adapter)
    discovery_handler = DiscoveryIntentHandler(adapter, resolver, CatalogSnapshotCache())
    cart_handler = CartIntentHandler(adapter)
    pending_gate = PendingActionGate(session_store, adapter)
    return DialogueContext(
        session_store=session_store,
        llm_client=llm_client,
        discovery_handler=discovery_handler,
        adapter=adapter,
        cart_handler=cart_handler,
        pending_gate=pending_gate,
    )


def _add_and_confirm(ctx: DialogueContext, session_id: str, text: str) -> None:
    handle_turn(ctx, session_id, text)
    handle_turn(ctx, session_id, "yes")


class _NonAliasingSessionStore(SessionStore):
    """SessionStore(redis_url=None) hands back the SAME ConversationSession object reference
    on every get_or_create() call for a given session_id, so two "independent" reads within
    one turn are actually aliases of one mutable object — real Redis deserializes a brand-new
    object on every read instead. That difference hid a real, confirmed bug: PendingActionGate
    .confirm() clears pending_action via its own independent read/write, but a handler that had
    already read the (now-stale) session earlier in the same call could still save it back
    afterward — silently resurrecting the just-spent PendingAction, confirmed=False again, so
    a stray later "yes" could re-trigger the SAME mutation a second time. Deep-copying on every
    read reproduces Redis's actual behavior without requiring a real Redis instance in tests."""

    def _read(self, session_id: str):
        session = self._memory.get(session_id)
        return copy.deepcopy(session) if session is not None else None


# -- Scenario 1: checkout request produces full recap + asks for confirmation -- #


def test_checkout_request_produces_full_recap_and_asks_for_confirmation(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    _add_and_confirm(ctx, "c1", "add the red classic t-shirt to my cart")

    reply = handle_turn(ctx, "c1", "checkout")

    assert "Classic T-Shirt" in reply
    assert "1" in reply
    assert "19.99" in reply
    assert "total" in reply.lower()
    assert "confirm" in reply.lower() or "shall i place" in reply.lower()

    session = session_store.get_or_create("c1")
    assert session.pending_action is not None
    assert session.pending_action.action_type == "checkout"
    assert len(adapter._orders) == 0  # no order placed yet


# -- Scenario 2: final confirmation places order, returns order id ------------ #


def test_confirming_checkout_places_order_and_reports_order_id(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    _add_and_confirm(ctx, "c2", "add the red classic t-shirt to my cart")
    handle_turn(ctx, "c2", "checkout")

    reply = handle_turn(ctx, "c2", "yes")

    assert "order" in reply.lower()
    assert len(adapter._orders) == 1
    (order,) = adapter._orders.values()
    assert order.id in reply

    cart = adapter.get_cart("c2")
    assert cart.lines == []  # cart cleared after a successful order

    session = session_store.get_or_create("c2")
    assert session.pending_action is None


# -- Scenario 3: requesting a change re-enters US2 flow, fresh recap needed --- #


def test_requesting_change_instead_of_confirming_requires_fresh_checkout_recap(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    _add_and_confirm(ctx, "c3", "add the red classic t-shirt to my cart")
    first_recap = handle_turn(ctx, "c3", "checkout")
    assert "1" in first_recap

    # Instead of confirming, the shopper asks to change the quantity (US2 flow).
    _add_and_confirm(ctx, "c3", "update the classic t-shirt quantity to 2")

    session = session_store.get_or_create("c3")
    assert session.pending_action is None  # the stale checkout proposal is gone

    # A stray "yes" now must not place an order against the old (1-unit) recap.
    reply = handle_turn(ctx, "c3", "yes")
    assert "nothing pending" in reply.lower()
    assert len(adapter._orders) == 0

    # Asking to checkout again produces a fresh recap reflecting the updated quantity.
    second_recap = handle_turn(ctx, "c3", "checkout")
    assert "2" in second_recap
    handle_turn(ctx, "c3", "yes")
    assert len(adapter._orders) == 1
    (order,) = adapter._orders.values()
    assert order.lines[0].quantity == 2


# -- Scenario 4: stock/price/promo change between recap and confirmation ----- #


def test_stock_change_between_recap_and_confirmation_forces_revalidation(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    _add_and_confirm(ctx, "c4", "add the blue jacket size m to my cart")
    handle_turn(ctx, "c4", "checkout")

    # The store's stock changes after the recap was shown but before confirmation.
    _, variant = adapter._find_variant("var-jacket-1-blue-m")
    variant.in_stock = False
    variant.stock_quantity = 0

    reply = handle_turn(ctx, "c4", "yes")

    assert len(adapter._orders) == 0  # no mismatched order placed
    assert "changed" in reply.lower() or "no longer" in reply.lower()
    assert "confirm" in reply.lower() or "shall i place" in reply.lower()

    # A fresh checkout PendingAction is presented instead of the stale one.
    session = session_store.get_or_create("c4")
    assert session.pending_action is not None
    assert session.pending_action.action_type == "checkout"


# -- Edge case: checkout with an empty cart, no recap shown (T043) ----------- #


def test_checkout_with_empty_cart_offers_to_resume_discovery_without_recap(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)

    reply = handle_turn(ctx, "c5", "checkout")

    assert "empty" in reply.lower()
    assert "total" not in reply.lower()  # no recap was shown
    session = session_store.get_or_create("c5")
    assert session.pending_action is None


# -- Session-write-clobber regression: a stray "yes" after checkout must never re-trigger -- #


def test_stray_confirm_after_checkout_does_not_re_place_the_order(
    adapter: MockAdapter, llm_client: RuleBasedStubClient
) -> None:
    """Regression test for a real bug found while investigating a live-reported "nothing
    pending" defect elsewhere in this same session-persistence pattern: `_handle_confirm`
    read `session` BEFORE calling `ctx.pending_gate.confirm()`, which independently clears
    pending_action as part of executing the checkout — then, on the checkout branch only,
    saved that now-stale `session` object again (just to set has_completed_order), silently
    resurrecting the already-spent, un-cleared PendingAction. Against a store where every
    get_or_create() returns a fresh object (real Redis; simulated here without needing one),
    a second, stray "yes" could then re-confirm and re-execute the SAME checkout — an actual
    double order — instead of correctly finding nothing pending."""
    session_store = _NonAliasingSessionStore(redis_url=None)
    ctx = _ctx(adapter, llm_client, session_store)
    _add_and_confirm(ctx, "c6", "add the red classic t-shirt to my cart")
    handle_turn(ctx, "c6", "checkout")
    handle_turn(ctx, "c6", "yes")
    assert len(adapter._orders) == 1

    stray_reply = handle_turn(ctx, "c6", "yes")

    assert len(adapter._orders) == 1  # not re-placed
    assert "nothing pending" in stray_reply.lower()
    session = session_store.get_or_create("c6")
    assert session.pending_action is None
    assert session.has_completed_order is True
