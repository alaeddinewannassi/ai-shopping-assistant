"""Integration tests for User Story 2 - Add to Cart with Confirmation (T028-T032a).

Exercises the full propose -> confirm/decline flow through agent/dialogue.py's
DialogueContext, the same wiring api/chat.py uses, backed by MockAdapter + PendingActionGate.
"""

from __future__ import annotations

import copy

import pytest

from src.adapters.mock import MockAdapter
from src.agent.dialogue import DialogueContext, handle_turn
from src.agent.intents import CartIntentHandler, CartResolutionKind, DiscoveryIntentHandler
from src.agent.llm_client import RuleBasedStubClient
from src.agent.pending import PendingActionGate
from src.agent.taxonomy_resolver import TaxonomyResolver
from src.session.catalog_cache import CatalogSnapshotCache
from src.session.store import SessionStore


class _NonAliasingSessionStore(SessionStore):
    """The base in-memory fallback (SessionStore(redis_url=None)) hands back the SAME
    ConversationSession object reference on every get_or_create() call for a given
    session_id — so two "independent" reads within one turn are actually aliases of one
    mutable object, and a write through either name is visible through the other. Real Redis
    does not work that way: every get_or_create() deserializes a BRAND NEW object from
    whatever's currently stored. That difference hid a real, confirmed live bug (a nested
    handler's just-committed pending_action got silently wiped by a later, stale full-session
    save elsewhere in the same turn) from the entire test suite. This subclass deep-copies on
    every read so tests can catch that class of bug without needing a real Redis instance."""

    def _read(self, session_id: str):
        session = self._memory.get(session_id)
        return copy.deepcopy(session) if session is not None else None


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
    # A genuinely confirmed cart mutation is worth a real navigation, not a redundant link.
    assert session.last_turn_auto_navigate_to_cart is True
    assert session.last_turn_shows_cart_link is False


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


def test_add_pronoun_with_variant_descriptor_resolves_against_the_single_last_shown_product(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    """Regression test for a real bug: "ok add me one in size M" left nothing but filler/
    quantity/variant words after stopword-cleaning ("one size m") — not an exact bare-pronoun
    match, and none of those words match any product name — producing a false NOT_FOUND even
    though exactly one product (Blue Jacket) had just been shown and was clearly what "one"
    referred to."""
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u14", "show me jackets")

    reply = handle_turn(ctx, "u14", "ok add me one in size M")

    assert "couldn't find a product" not in reply.lower()
    assert "Blue Jacket" in reply


def test_short_leftover_term_falls_back_to_single_last_shown_product(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    """Regression test for a real bug reported from live testing: after "show me jackets"
    shows exactly one jacket, a vague confirmation like "please add it, go for it" cleans
    down to "it go it" after cart-stopword-stripping — no token longer than 2 chars survives.
    Both CommerceAdapter implementations treat a query with no such token as "nothing
    meaningful to filter on" and deliberately return the WHOLE catalog unfiltered (reasonable
    for a bare discovery browse like "show me what you have") — but _resolve_single_product
    was calling search_products with exactly that leftover term, so `products` was never
    empty and its "single last-shown item -> default to it" fallback never triggered. Live,
    this surfaced as an unrelated, catalog-wide "did you mean" list (t-shirts, framed
    posters, ...) instead of the one item the shopper had just been shown and was clearly
    replying about."""
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u18", "show me jackets")

    reply = handle_turn(ctx, "u18", "please add it, go for it")

    assert "couldn't find a product" not in reply.lower()
    assert "Blue Jacket" in reply
    session = session_store.get_or_create("u18")
    # Resolved against the SPECIFIC last-shown jacket, not an arbitrary catalog-wide guess —
    # this is what a "did you mean: <random unrelated products>" reply would have failed.
    assert session.pending_variant_product_name == "Blue Jacket"


def test_hyphenless_product_name_correctly_narrows_to_one_match(adapter: MockAdapter) -> None:
    """Regression test for a real bug: "add the tshirt not the jacket" correctly found the
    t-shirt in the initial broad search (fold-matching handles "tshirt" vs "t-shirt"), but
    the AND-narrowing step used a naive literal substring check ("tshirt" in
    "classic t-shirt" is False — the hyphen makes them different strings) instead of the
    same matching, so it never actually narrowed down and stayed stuck "ambiguous" — even
    though only one candidate ever matched both tokens in the first place. Calls
    CartIntentHandler directly (bypassing intent classification) to isolate the resolution
    logic under test — RuleBasedStubClient's regex matcher can't infer "add" intent from
    context the way a real LLM does, that's a separate concern from this fix."""
    handler = CartIntentHandler(adapter)

    resolution = handler.resolve_add_to_cart("add the tshirt not the jacket")

    assert resolution.kind == CartResolutionKind.AMBIGUOUS_VARIANT
    assert resolution.product is not None
    assert resolution.product.name == "Classic T-Shirt"


def test_cart_action_marks_the_turn_as_cart_link_worthy(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "u15", "add the red classic t-shirt to my cart")

    session = session_store.get_or_create("u15")
    assert session.last_turn_shows_cart_link is True
    assert session.last_turn_product_ids == []  # this is a cart link, not a product link
    # The initial propose still needs a yes/no answer — never auto-navigate before that.
    assert session.last_turn_auto_navigate_to_cart is False


# -- Pending variant-clarification memory across turns ------------------------------------ #


def test_bare_variant_answer_resolves_against_the_product_still_being_asked_about(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    """Regression test for the real purchase-blocking bug from a shared transcript: a shopper
    asked to add the Classic T-Shirt (two variants, Red/M and Blue/M — color unspecified is
    genuinely ambiguous), was asked which option they meant, and answered with just a bare
    attribute ("red") with no product name in it. Previously that answer had no memory of
    which product it was even answering for, so it fell back to whatever was last
    searched/shown (possibly nothing, or something else entirely) instead of resolving
    against the Classic T-Shirt the clarifying question was actually about."""
    ctx = _ctx(adapter, llm_client, session_store)

    first_reply = handle_turn(ctx, "u16", "add the classic t-shirt")
    assert "Which option of Classic T-Shirt did you mean" in first_reply

    session = session_store.get_or_create("u16")
    assert session.pending_variant_product_name == "Classic T-Shirt"
    assert session.pending_variant_product_id is not None

    second_reply = handle_turn(ctx, "u16", "red")

    assert "couldn't find a product" not in second_reply.lower()
    assert "Classic T-Shirt" in second_reply
    assert "confirm" in second_reply.lower() or "yes" in second_reply.lower()

    # The open question has been answered — nothing should linger to misdirect a later,
    # unrelated turn.
    session = session_store.get_or_create("u16")
    assert session.pending_variant_product_id is None

    confirm_reply = handle_turn(ctx, "u16", "yes")
    assert "Classic T-Shirt" in confirm_reply
    cart = adapter.get_cart("u16")
    assert len(cart.lines) == 1
    assert cart.lines[0].variant_id == "var-tshirt-1-red-m"


# -- Session-write-clobber regression: a second propose mid-conversation must not silently -- #
# -- wipe itself out before the shopper can confirm it -------------------------------------- #


def test_second_propose_survives_to_be_confirmed_against_a_non_aliasing_session_store(
    adapter: MockAdapter, llm_client: RuleBasedStubClient
) -> None:
    """Regression test for a real, confirmed live bug reported from an actual deployed
    session: a shopper proposed adding an item, then (before confirming) proposed adding it
    again with a different quantity — the second recap was shown correctly, but the
    following "yes" replied "There's nothing pending for me to confirm right now." and the
    cart stayed empty.

    Root cause: `_route_turn` reads the session ONCE at the top of the turn, but several
    handlers it calls (`_handle_propose_add_to_cart` -> `PendingActionGate.propose` ->
    `SessionStore.propose_action`) do their OWN independent get_or_create()+save() round
    trips on the same session_id. Against real Redis, every get_or_create() deserializes a
    brand-new object — so `_route_turn`'s final, unconditional save of its now-stale
    top-of-turn snapshot silently wiped out the pending_action a nested call had just
    committed, moments earlier, in the very same turn.

    `SessionStore(redis_url=None)` (used by every other test in this suite) hides this
    entirely — its in-memory fallback hands back the SAME object reference on every read, so
    two "independent" reads are actually aliases of one mutable object and nothing is ever
    lost. `_NonAliasingSessionStore` (this file) deep-copies on every read specifically to
    catch this class of bug without requiring a real Redis instance."""
    session_store = _NonAliasingSessionStore(redis_url=None)
    ctx = _ctx(adapter, llm_client, session_store)

    handle_turn(ctx, "u17", "add the red classic t-shirt to my cart")
    second_reply = handle_turn(ctx, "u17", "add 2 red classic t-shirts to my cart")
    assert "2 x Classic T-Shirt" in second_reply

    confirm_reply = handle_turn(ctx, "u17", "yes")

    assert "nothing pending" not in confirm_reply.lower()
    cart = adapter.get_cart("u17")
    assert len(cart.lines) == 1
    assert cart.lines[0].quantity == 2  # the SECOND propose, not a stale/wiped first one
