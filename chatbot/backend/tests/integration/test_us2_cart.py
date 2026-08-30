"""Integration tests for User Story 2 - Add to Cart with Confirmation (T028-T032a).

Exercises the full propose -> confirm/decline flow through agent/dialogue.py's
DialogueContext, the same wiring api/chat.py uses, backed by MockAdapter + PendingActionGate.
"""

from __future__ import annotations

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


# -- Scenario 1: add-to-cart request produces confirmation, no mutation yet -- #


def test_add_to_cart_request_produces_confirmation_without_mutating(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    reply = handle_turn(ctx, "u1", "add the red classic t-shirt to my cart")

    assert "Classic T-Shirt" in reply
    assert "confirm" in reply.lower() or "yes" in reply.lower()

    cart = adapter.get_cart("u1")
    assert cart.lines == []  # no mutation before confirmation

    session = session_store.get_or_create("u1")
    assert session.pending_action is not None
    assert session.pending_action.action_type == "add_cart_item"


class _LyingLLMClient(RuleBasedStubClient):
    """A real, confirmed failure mode (not hypothetical): asked to rephrase an unresolved
    clarifying question, a real LLM once fabricated "Got it — I've added ... to your cart."
    This proves dialogue.py's routing never even calls phrase_reply for a cart action in the
    first place — regardless of what any LLMClient's phrase_reply might say, the reply that
    reaches the shopper for propose_add_to_cart must always be the exact template."""

    def phrase_reply(self, facts: str, shopper_message: str, *, session_id: str | None = None) -> str:
        return "Got it — I've added that to your cart!"


def test_propose_add_to_cart_reply_is_never_handed_to_phrase_reply(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, _LyingLLMClient(), session_store)
    reply = handle_turn(ctx, "u1b", "add the red classic t-shirt to my cart")

    assert reply != "Got it — I've added that to your cart!"
    assert "confirm" in reply.lower() or "yes" in reply.lower()
    assert adapter.get_cart("u1b").lines == []  # still nothing actually added


# -- Scenario 2: confirming adds the item; cart reflects it ------------------ #


def test_confirming_add_to_cart_mutates_cart(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u2", "add the red classic t-shirt to my cart")
    reply = handle_turn(ctx, "u2", "yes")

    assert "Classic T-Shirt" in reply
    cart = adapter.get_cart("u2")
    assert len(cart.lines) == 1
    assert cart.lines[0].variant_id == "var-tshirt-1-red-m"
    assert cart.lines[0].quantity == 1

    session = session_store.get_or_create("u2")
    assert session.pending_action is None  # spent after confirm


# -- Scenario 3: declining leaves cart untouched, offers a corrected option -- #


def test_declining_add_to_cart_leaves_cart_untouched(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u3", "add the red classic t-shirt to my cart")
    reply = handle_turn(ctx, "u3", "no, cancel that")

    assert "won't" in reply.lower() or "no problem" in reply.lower()
    cart = adapter.get_cart("u3")
    assert cart.lines == []

    session = session_store.get_or_create("u3")
    assert session.pending_action is None


def test_stray_confirmation_after_decline_does_not_mutate(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    """A later, unrelated 'yes' must never resurrect a declined proposal (research.md §9.4)."""
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u3b", "add the red classic t-shirt to my cart")
    handle_turn(ctx, "u3b", "no, cancel that")
    reply = handle_turn(ctx, "u3b", "yes")

    assert "nothing pending" in reply.lower()
    cart = adapter.get_cart("u3b")
    assert cart.lines == []


# -- Scenario 4: update quantity / remove line, same confirm-before-mutate --- #


def test_update_cart_line_quantity_follows_confirm_before_mutate(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u4", "add the red classic t-shirt to my cart")
    handle_turn(ctx, "u4", "yes")

    reply = handle_turn(ctx, "u4", "update the classic t-shirt quantity to 3")
    assert "3" in reply
    cart = adapter.get_cart("u4")
    assert cart.lines[0].quantity == 1  # unchanged before confirmation

    handle_turn(ctx, "u4", "yes")
    cart = adapter.get_cart("u4")
    assert cart.lines[0].quantity == 3


def test_remove_cart_line_follows_confirm_before_mutate(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u5", "add the red classic t-shirt to my cart")
    handle_turn(ctx, "u5", "yes")

    reply = handle_turn(ctx, "u5", "remove the classic t-shirt")
    assert "remove" in reply.lower()
    cart = adapter.get_cart("u5")
    assert len(cart.lines) == 1  # unchanged before confirmation

    handle_turn(ctx, "u5", "yes")
    cart = adapter.get_cart("u5")
    assert cart.lines == []


# -- Scenario 5: out-of-stock product reports unavailability, no mutation --- #


def test_out_of_stock_variant_reports_unavailability_with_alternatives(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    # var-jacket-1-blue-l is seeded out of stock; var-jacket-1-blue-m is in stock.
    reply = handle_turn(ctx, "u6", "add the blue jacket size l to my cart")

    assert "out of stock" in reply.lower()
    assert "size: m" in reply.lower() or "m" in reply.lower()

    session = session_store.get_or_create("u6")
    assert session.pending_action is None  # never propose a mutation for an OOS item
    cart = adapter.get_cart("u6")
    assert cart.lines == []


# -- Edge case: store backend unreachable during add/update/remove (T032a) -- #


def test_backend_unreachable_during_add_to_cart_refuses_plainly_no_pending_action(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    adapter.simulate_outage(True)

    reply = handle_turn(ctx, "u7", "add the red classic t-shirt to my cart")

    assert "can't reach" in reply.lower() or "can't verify" in reply.lower()
    session = session_store.get_or_create("u7")
    assert session.pending_action is None


def test_backend_unreachable_during_confirm_does_not_assume_success(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u8", "add the red classic t-shirt to my cart")

    adapter.simulate_outage(True)
    reply = handle_turn(ctx, "u8", "yes")

    assert "couldn't apply" in reply.lower() or "unreachable" in reply.lower()

    # The PendingAction must be spent (not silently retryable/resurrectable) even though the
    # mutation itself failed — matches PendingActionGate.confirm()'s finally-clear semantics.
    session = session_store.get_or_create("u8")
    assert session.pending_action is None

    adapter.simulate_outage(False)
    assert adapter.get_cart("u8").lines == []  # never assumed success


# -- Reference resolution: "add it" / "the first one" against what was just shown -------- #


def test_add_it_resolves_against_the_single_last_shown_product(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u9", "show me jackets")

    reply = handle_turn(ctx, "u9", "add it to my cart")

    assert "couldn't find a product" not in reply.lower()
    assert "Blue Jacket" in reply


def test_add_the_first_one_resolves_by_ordinal(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u10", "show me jackets")

    reply = handle_turn(ctx, "u10", "add the first one to my cart")

    assert "couldn't find a product" not in reply.lower()
    assert "Blue Jacket" in reply
