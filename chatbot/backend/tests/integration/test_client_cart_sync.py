"""Integration tests for client-cart-sync (specs/003-adversarial-qa-review).

Regression coverage for a real bug found via adversarial review: the chatbot's own
webservice-created cart and PrestaShop's real front-end session cart are completely
disconnected — a shopper could confirm an add via chat, be told "Your cart now has...", and
still see an EMPTY cart on the store's own cart page. These tests exercise the full
handle_turn() flow (the same wiring api/chat.py uses) with a client_cart_snapshot supplied,
the way the real widget does, and confirm the resulting ChatResponse-equivalent session
fields (last_turn_client_cart_action, last_turn_handoff_to_native_checkout) carry the right
instruction for the widget to execute — never that the adapter itself was mutated.
"""

from __future__ import annotations

from src.adapters.base import Cart, CartLine
from src.adapters.mock import MockAdapter
from src.agent.dialogue import DialogueContext, handle_turn
from src.agent.intents import CartIntentHandler, DiscoveryIntentHandler
from src.agent.llm_client import ActionCall
from src.agent.pending import PendingActionGate
from src.agent.taxonomy_resolver import TaxonomyResolver
from src.session.catalog_cache import CatalogSnapshotCache
from src.session.store import SessionStore


class _ClientSyncAdapter(MockAdapter):
    """MockAdapter + client-cart-sync support, raising if a mutation the sync path should
    bypass is ever called directly."""

    supports_client_cart_sync = True

    def cart_from_snapshot(self, snapshot: list[dict]) -> Cart:
        lines = [
            CartLine(
                product_id=row["variant_id"].split("#")[0],
                variant_id=row["variant_id"],
                quantity=row["quantity"],
                unit_price=1.0,
            )
            for row in snapshot
        ]
        return Cart(id="client-cart", lines=lines)

    def add_cart_item(self, *a, **k):
        raise AssertionError("client-synced session must never mutate the adapter directly")

    def checkout(self, *a, **k):
        raise AssertionError("client-synced session must never mutate the adapter directly")


class _ScriptedLLMClient:
    """Returns `action` UNLESS the context says a pending action already awaits
    confirmation, in which case it confirms — mirrors what a real LLM (or
    RuleBasedStubClient) does for a bare "yes" following a proposal, without needing this
    test to hardcode routing logic for every message."""

    def __init__(self, action: ActionCall) -> None:
        self._action = action

    def parse_turn(self, message: str, context: dict, *, session_id: str | None = None) -> ActionCall:
        if context.get("pending_action") is not None:
            return ActionCall(action_type="confirm_pending_action", parameters={})
        return self._action

    def phrase_reply(self, facts: str, shopper_message: str, *, session_id: str | None = None) -> str:
        return facts


def _ctx(adapter: MockAdapter, llm_client, session_store: SessionStore) -> DialogueContext:
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


def test_confirmed_add_produces_a_widget_cart_action_and_never_touches_the_adapters_own_cart() -> None:
    adapter = _ClientSyncAdapter()
    session_store = SessionStore(redis_url=None)
    llm_client = _ScriptedLLMClient(
        ActionCall(action_type="propose_add_to_cart", parameters={"raw_text": "I'll take the red classic t-shirt"})
    )
    ctx = _ctx(adapter, llm_client, session_store)

    reply = handle_turn(
        ctx, "s1", "I'll take the red classic t-shirt",
        cart_snapshot=[],  # the shopper's real (currently empty) cart, widget-reported
    )
    assert "confirm" in reply.lower() or "add" in reply.lower()

    session = session_store.get_or_create("s1")
    assert session.client_cart_snapshot == []
    assert session.pending_action is not None

    reply = handle_turn(ctx, "s1", "yes", cart_snapshot=[])

    session = session_store.get_or_create("s1")
    assert session.last_turn_client_cart_action is not None
    assert session.last_turn_client_cart_action["op"] == "increment"
    assert session.last_turn_client_cart_action["quantity"] == 1
    # The predicted-cart recap still gets shown, from the (real) pre-mutation snapshot —
    # not from an adapter call that never happened.
    assert "cart" in reply.lower()


def test_a_caller_that_never_sends_a_snapshot_keeps_todays_backend_owned_cart_behavior() -> None:
    """A non-browser API caller (curl, a script, a mobile app with no PrestaShop session)
    must be completely unaffected by client-cart-sync — this is opt-in per session, driven
    by whether a snapshot was ever actually provided, not by adapter capability alone."""
    real_adapter = MockAdapter()

    class _CapableButUnused(_ClientSyncAdapter):
        def add_cart_item(self, cart_id, product_id, variant_id, quantity):
            return real_adapter.add_cart_item(cart_id, product_id, variant_id, quantity)

    adapter = _CapableButUnused()
    session_store = SessionStore(redis_url=None)
    llm_client = _ScriptedLLMClient(
        ActionCall(action_type="propose_add_to_cart", parameters={"raw_text": "I'll take the red classic t-shirt"})
    )
    ctx = _ctx(adapter, llm_client, session_store)

    handle_turn(ctx, "s2", "I'll take the red classic t-shirt")  # no cart_snapshot kwarg at all
    handle_turn(ctx, "s2", "yes")

    session = session_store.get_or_create("s2")
    assert session.client_cart_snapshot is None
    assert session.last_turn_client_cart_action is None
    # The real adapter mutation DID happen, via the normal (pre-sync) path.
    cart = real_adapter.get_cart("s2")
    assert len(cart.lines) == 1


def test_confirmed_checkout_hands_off_to_native_checkout_for_a_synced_session() -> None:
    adapter = _ClientSyncAdapter()
    session_store = SessionStore(redis_url=None)
    llm_client = _ScriptedLLMClient(ActionCall(action_type="request_checkout", parameters={}))
    ctx = _ctx(adapter, llm_client, session_store)

    handle_turn(
        ctx, "s3", "checkout",
        cart_snapshot=[{"variant_id": "prod-tshirt-1#var-tshirt-1-red-m", "quantity": 1}],
    )
    session = session_store.get_or_create("s3")
    assert session.pending_action is not None

    handle_turn(ctx, "s3", "yes", cart_snapshot=session.client_cart_snapshot)

    session = session_store.get_or_create("s3")
    assert session.last_turn_handoff_to_native_checkout is True
    assert session.has_completed_order is False  # no order was placed here
