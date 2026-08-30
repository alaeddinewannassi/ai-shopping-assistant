"""Integration tests for User Story 4 - Strategic Promo Code Suggestions (T051-T055).

Exercises the full suggestion -> validate -> confirm -> apply flow, and the manual
shopper-provided code path, through agent/dialogue.py's DialogueContext, the same wiring
api/chat.py uses, backed by MockAdapter + PendingActionGate.
"""

from __future__ import annotations

import pytest

from src.adapters.mock import MockAdapter
from src.agent.dialogue import DialogueContext, handle_turn
from src.agent.intents import CartIntentHandler, DiscoveryIntentHandler, PromoIntentHandler
from src.agent.llm_client import RuleBasedStubClient
from src.agent.pending import PendingActionGate
from src.agent.taxonomy_resolver import TaxonomyResolver
from src.promo.strategy import PromoStrategyRule
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


@pytest.fixture
def promo_rules() -> list[PromoStrategyRule]:
    return [
        PromoStrategyRule(
            rule_id="welcome-first-order", condition="first_order and subtotal > 0",
            target_code="WELCOME10", priority=5,
        ),
        PromoStrategyRule(
            rule_id="big-cart", condition="subtotal >= 100", target_code="BIGCART15", priority=10,
        ),
    ]


def _ctx(
    adapter: MockAdapter,
    llm_client: RuleBasedStubClient,
    session_store: SessionStore,
    promo_rules: list[PromoStrategyRule],
) -> DialogueContext:
    resolver = TaxonomyResolver(adapter)
    discovery_handler = DiscoveryIntentHandler(adapter, resolver, CatalogSnapshotCache())
    cart_handler = CartIntentHandler(adapter)
    pending_gate = PendingActionGate(session_store, adapter)
    promo_handler = PromoIntentHandler(adapter)
    return DialogueContext(
        session_store=session_store,
        llm_client=llm_client,
        discovery_handler=discovery_handler,
        adapter=adapter,
        cart_handler=cart_handler,
        pending_gate=pending_gate,
        promo_handler=promo_handler,
        promo_rules=promo_rules,
    )


def _add_and_confirm(ctx: DialogueContext, session_id: str, text: str) -> None:
    handle_turn(ctx, session_id, text)
    handle_turn(ctx, session_id, "yes")


# -- Scenario 1: cart matches a rule -> proactive suggestion with benefit ----- #


def test_cart_matching_rule_gets_proactive_suggestion_with_benefit(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore,
    promo_rules: list[PromoStrategyRule],
) -> None:
    ctx = _ctx(adapter, llm_client, session_store, promo_rules)
    _add_and_confirm(ctx, "p1", "add the blue jacket size m to my cart")
    _add_and_confirm(ctx, "p1", "update the blue jacket quantity to 2")  # 2 x 89.99 = 179.98 >= 100

    reply = handle_turn(ctx, "p1", "what else do you have?")

    assert "BIGCART15" in reply
    assert "$" in reply  # benefit explanation includes the savings amount

    session = session_store.get_or_create("p1")
    assert session.pending_action is not None
    assert session.pending_action.action_type == "apply_promo"


# -- Scenario 2: accepting -> store-validated before reflected in total ------ #


def test_accepting_suggestion_validates_before_reflecting_in_total(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore,
    promo_rules: list[PromoStrategyRule],
) -> None:
    ctx = _ctx(adapter, llm_client, session_store, promo_rules)
    _add_and_confirm(ctx, "p2", "add the blue jacket size m to my cart")
    _add_and_confirm(ctx, "p2", "update the blue jacket quantity to 2")

    handle_turn(ctx, "p2", "what else do you have?")  # triggers the BIGCART15 suggestion
    reply = handle_turn(ctx, "p2", "yes")

    assert "applied" in reply.lower() or "$" in reply
    cart = adapter.get_cart("p2")
    assert cart.applied_promo_code == "BIGCART15"
    assert cart.discount_total > 0
    assert cart.grand_total < cart.subtotal


# -- Scenario 3: declining -> no code applied, original total stands --------- #


def test_declining_suggestion_leaves_total_unchanged(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore,
    promo_rules: list[PromoStrategyRule],
) -> None:
    ctx = _ctx(adapter, llm_client, session_store, promo_rules)
    _add_and_confirm(ctx, "p3", "add the blue jacket size m to my cart")
    _add_and_confirm(ctx, "p3", "update the blue jacket quantity to 2")

    handle_turn(ctx, "p3", "what else do you have?")
    original_subtotal = adapter.get_cart("p3").subtotal
    handle_turn(ctx, "p3", "no thanks")

    cart = adapter.get_cart("p3")
    assert cart.applied_promo_code is None
    assert cart.discount_total == 0
    assert cart.subtotal == original_subtotal


# -- Scenario 4: shopper-provided code, validated the same way --------------- #


def test_shopper_provided_valid_code_is_applied_after_validation(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore,
    promo_rules: list[PromoStrategyRule],
) -> None:
    ctx = _ctx(adapter, llm_client, session_store, promo_rules)
    _add_and_confirm(ctx, "p4", "add the red classic t-shirt to my cart")

    reply = handle_turn(ctx, "p4", "apply promo code WELCOME10")
    assert "WELCOME10" in reply
    assert "confirm" in reply.lower()

    cart_before = adapter.get_cart("p4")
    assert cart_before.applied_promo_code is None  # not applied before confirmation

    handle_turn(ctx, "p4", "yes")
    cart = adapter.get_cart("p4")
    assert cart.applied_promo_code == "WELCOME10"
    assert cart.discount_total > 0


def test_shopper_provided_invalid_code_reports_reason_clearly(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore,
    promo_rules: list[PromoStrategyRule],
) -> None:
    ctx = _ctx(adapter, llm_client, session_store, promo_rules)
    _add_and_confirm(ctx, "p5", "add the red classic t-shirt to my cart")

    reply = handle_turn(ctx, "p5", "apply promo code FAKE99")

    assert "fake99" in reply.lower()
    assert "not" in reply.lower() or "invalid" in reply.lower()
    session = session_store.get_or_create("p5")
    assert session.pending_action is None
    cart = adapter.get_cart("p5")
    assert cart.applied_promo_code is None


# -- Scenario 5: no rule matches -> honest "no discount available" ---------- #


def test_no_matching_rule_reports_no_discount_available(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore,
    promo_rules: list[PromoStrategyRule],
) -> None:
    ctx = _ctx(adapter, llm_client, session_store, promo_rules)
    # Complete one small order first, so this session is no longer "first order" (the
    # welcome-first-order rule would otherwise always match a non-empty cart).
    _add_and_confirm(ctx, "p6", "add the red classic t-shirt to my cart")
    handle_turn(ctx, "p6", "checkout")
    handle_turn(ctx, "p6", "yes")

    # A second, small cart — well under the BIGCART15 threshold, no longer first order.
    _add_and_confirm(ctx, "p6", "add the red classic t-shirt to my cart")

    reply = handle_turn(ctx, "p6", "is there a discount code available?")

    assert "no" in reply.lower() or "don't see" in reply.lower()
    assert "WELCOME10" not in reply
    assert "BIGCART15" not in reply
