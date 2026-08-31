"""Integration tests for User Story 1 - Conversational Product Discovery & Navigation
(T018-T021a). Exercises the full stack: RuleBasedStubClient -> DiscoveryIntentHandler ->
TaxonomyResolver -> MockAdapter -> dialogue.handle_turn, the same wiring api/chat.py uses.
"""

from __future__ import annotations

import pytest

from src.adapters.base import AttributeGroup, Category
from src.adapters.mock import MockAdapter
from src.agent.dialogue import DialogueContext, handle_turn
from src.agent.intents import DiscoveryIntentHandler
from src.agent.llm_client import ActionCall, RuleBasedStubClient
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
    return DialogueContext(
        session_store=session_store,
        llm_client=llm_client,
        discovery_handler=discovery_handler,
        adapter=adapter,
    )


# -- Scenario 1: category + price constraint search --------------------------- #


def test_search_by_category_and_price_constraint_returns_matching_products(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(_ctx(adapter, llm_client, session_store), "s1", "show me jackets under $100")
    assert "Blue Jacket" in reply


def test_search_by_category_and_price_constraint_excludes_out_of_range_products(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(_ctx(adapter, llm_client, session_store), "s1", "show me jackets under $50")
    assert "Blue Jacket" not in reply
    assert "couldn't find" in reply


# -- Scenario 2: navigate to a named category/product ------------------------- #


def test_navigate_to_named_category(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(_ctx(adapter, llm_client, session_store), "s2", "take me to the t-shirts category")
    assert "T-Shirts" in reply or "t-shirts" in reply.lower()
    assert "Classic T-Shirt" in reply

    session = session_store.get_or_create("s2")
    assert session.navigation_context.get("category_id") == "cat-tshirts"


def test_navigate_to_named_product(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(_ctx(adapter, llm_client, session_store), "s3", "go to Blue Jacket")
    assert "Blue Jacket" in reply


# -- Scenario 3: ambiguous request -> exactly one clarifying question --------- #


def test_ambiguous_category_term_triggers_one_clarifying_question(
    llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    adapter = MockAdapter()
    # Seed a second, deliberately overlapping category so "jacket" is genuinely ambiguous
    # between two real categories — exercising ResolutionStatus.AMBIGUOUS end-to-end.
    adapter._categories["cat-rain-jackets"] = Category(id="cat-rain-jackets", name="Rain Jackets")

    reply = handle_turn(_ctx(adapter, llm_client, session_store), "s4", "show me jackets")
    assert "?" in reply
    assert reply.count("?") == 1  # at most one clarifying question (FR-003)
    assert "Jackets" in reply and "Rain Jackets" in reply


# -- Scenario 4: no catalog matches -> plain message, no dead-end navigation --- #


def test_no_matches_returns_plain_message_not_dead_end(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(_ctx(adapter, llm_client, session_store), "s5", "show me nonexistent gizmos")
    assert "couldn't find" in reply
    assert "try" in reply.lower()

    # No dead-end: navigation_context must remain unset (we did not silently "navigate"
    # anywhere on a zero-match search).
    session = session_store.get_or_create("s5")
    assert session.navigation_context == {}


def test_empty_umbrella_category_false_match_falls_back_to_keyword_search(
    llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    """Real bug: a full sentence that merely mentions a category word in passing
    ("...outerwear...") can substring-match an umbrella category with no directly-attached
    products (taxonomy_resolver._normalize is a loose substring check, fine for a short
    category term but not for an arbitrary sentence). That empty "exact" match must not
    shadow a real keyword match sitting elsewhere in the catalog."""
    adapter = MockAdapter()
    adapter._categories["cat-outerwear"] = Category(id="cat-outerwear", name="Outerwear")

    reply = handle_turn(
        _ctx(adapter, llm_client, session_store),
        "s4b",
        "looking for something in outerwear, maybe the classic shirt",
    )
    assert "Classic T-Shirt" in reply
    assert "couldn't find" not in reply


# -- Edge case: store backend unreachable during discovery (T021a, FR-016) ---- #


def test_backend_unreachable_falls_back_to_cached_snapshot_with_disclaimer(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)

    # Warm the cache with a successful live search first.
    warm_up = handle_turn(ctx, "s6", "show me jackets")
    assert "Blue Jacket" in warm_up

    adapter.simulate_outage(True)
    reply = handle_turn(ctx, "s6", "show me jackets")

    assert "may be outdated" in reply.lower() or "cached" in reply.lower()
    assert "Blue Jacket" in reply


def test_backend_unreachable_with_nothing_cached_gives_plain_unavailable_message(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, llm_client, session_store)
    adapter.simulate_outage(True)

    reply = handle_turn(ctx, "s7", "show me completely new query xyz")

    assert "can't reach" in reply.lower() or "can't search" in reply.lower()
    assert "Jacket" not in reply and "T-Shirt" not in reply  # never fabricate product data


# -- ask_or_chat: the conversational fallback for greetings/small talk -------- #


class _ScriptedLLMClient:
    """A minimal LLMClient stub (duck-typed against the Protocol) that always returns one
    fixed ActionCall — RuleBasedStubClient has no ask_or_chat branch (by design, see its own
    docstring), so dialogue.py's routing for it needs a client that can actually produce one."""

    def __init__(self, action: ActionCall) -> None:
        self._action = action

    def parse_turn(self, message: str, context: dict, *, session_id: str | None = None) -> ActionCall:
        return self._action

    def phrase_reply(self, facts: str, shopper_message: str, *, session_id: str | None = None) -> str:
        return facts


class _LyingScriptedLLMClient(_ScriptedLLMClient):
    """A real, confirmed failure mode (not hypothetical): asked to rephrase get_product_details'
    own CLARIFY outcome (multiple candidates, nothing resolved), a real LLM once fabricated
    "Got it — here's the Hummingbird printed sweater you asked about." — a false resolution
    claim. Proves dialogue.py's routing never even calls phrase_reply for a CLARIFY outcome in
    the first place — regardless of what any LLMClient's phrase_reply might say."""

    def phrase_reply(self, facts: str, shopper_message: str, *, session_id: str | None = None) -> str:
        return "Got it — here's the exact item you asked about."


class _RenamingScriptedLLMClient(_ScriptedLLMClient):
    """A real, confirmed live failure mode (not hypothetical): asked to rephrase a
    single-product search result, a real LLM substituted a descriptive paraphrase for the
    catalog's actual product name ("Classic T-Shirt" became "a round-neck tee") — a subtle
    violation of _PHRASE_SYSTEM_PROMPT's explicit "never change a product name" rule. Proves
    dialogue.py verifies phrase_reply's output still contains the real product name,
    discarding it (falling back to the exact template) rather than trusting the prompt
    alone."""

    def phrase_reply(self, facts: str, shopper_message: str, *, session_id: str | None = None) -> str:
        return "I found a great round-neck tee for you, only $19.99!"


def test_phrase_reply_that_renames_the_product_is_discarded(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    scripted = _RenamingScriptedLLMClient(
        ActionCall(action_type="search_products", parameters={"query": "classic t-shirt"})
    )
    ctx = _ctx(adapter, scripted, session_store)

    reply = handle_turn(ctx, "s18", "classic t-shirt")

    assert "Classic T-Shirt" in reply
    assert "round-neck" not in reply.lower()


def test_ask_or_chat_returns_the_llms_text_directly(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    scripted = _ScriptedLLMClient(
        ActionCall(action_type="ask_or_chat", parameters={"text": "Hi! What are you shopping for today?"})
    )
    ctx = _ctx(adapter, scripted, session_store)

    reply = handle_turn(ctx, "s8", "hello")

    assert reply == "Hi! What are you shopping for today?"


def test_ask_or_chat_falls_back_to_a_default_when_text_is_missing(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    scripted = _ScriptedLLMClient(ActionCall(action_type="ask_or_chat", parameters={}))
    ctx = _ctx(adapter, scripted, session_store)

    reply = handle_turn(ctx, "s9", "hello")

    assert reply == "How can I help you find something today?"


def test_a_real_search_result_is_remembered_as_context_for_the_next_turn(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    """A follow-up question right after a search result (e.g. "does it fit a young man?")
    needs something to connect "it" to — this is what feeds that into the LLM's context on
    the NEXT turn (dialogue.py's _build_llm_context / _record_navigation)."""
    ctx = _ctx(adapter, llm_client, session_store)
    handle_turn(ctx, "s10", "show me jackets")

    session = session_store.get_or_create("s10")
    assert "Blue Jacket" in session.last_shown_products


def test_get_product_details_reports_real_variant_data_for_the_last_shown_product(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    """Regression test for a real bug: asking "what sizes do you have" right after a search
    result was being misrouted through search_products, which rewrote the query using
    context ("Hummingbird printed t-shirt sizes") and matched unrelated products instead of
    answering the question. get_product_details answers it directly with real data."""
    session = session_store.get_or_create("s12")
    session.last_shown_product_ids = ["prod-jacket-1"]
    session_store.save(session)

    scripted = _ScriptedLLMClient(
        ActionCall(action_type="get_product_details", parameters={"raw_text": "what sizes do you have"})
    )
    ctx = _ctx(adapter, scripted, session_store)

    reply = handle_turn(ctx, "s12", "what sizes do you have")

    assert "Blue Jacket" in reply
    assert "size: M" in reply
    assert "size: L" in reply
    assert "out of stock" in reply  # the L variant is out of stock — a real, not guessed, fact


def test_get_product_details_reply_includes_the_real_catalog_description(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    """Regression test for a real gap reported from live testing: "is it cotton?" got a
    generic "I couldn't find anything matching that" instead of an answer, because the
    catalog description phrase_reply would need to answer from wasn't even fetched or
    included anywhere in get_product_details' reply. It doesn't need to be phrased into a
    yes/no here (that's phrase_reply's job, exercised against a real LLM only) — just
    present as real, verifiable ground truth in the deterministic reply."""
    session = session_store.get_or_create("s12b")
    session.last_shown_product_ids = ["prod-tshirt-1"]
    session_store.save(session)

    scripted = _ScriptedLLMClient(
        ActionCall(action_type="get_product_details", parameters={"raw_text": "is it cotton"})
    )
    ctx = _ctx(adapter, scripted, session_store)

    reply = handle_turn(ctx, "s12b", "is it cotton")

    assert "cotton" in reply.lower()


def test_named_reference_with_trailing_chatter_resolves_against_the_last_shown_product(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    """Regression test for a real live bug: "the adventure begins one, does it come in
    different sizes?" (a named reference to a just-shown product, plus a trailing question)
    went ambiguous because a generic word in the trailing question ("come") happened to
    OR-match a completely different, unrelated product's own name ("...yet to come'"
    poster) — and the AND-narrowing step couldn't collapse the resulting candidates back to
    one, since no single product matches every noisy token. Whichever candidate was
    actually just shown to the shopper is a much stronger disambiguating signal than a
    keyword collision with an unrelated product."""
    session = session_store.get_or_create("s12c")
    session.last_shown_product_ids = ["prod-jacket-1"]
    session_store.save(session)

    scripted = _ScriptedLLMClient(
        ActionCall(
            action_type="get_product_details",
            parameters={"raw_text": "the jacket one, is it 100% cotton like the other item"},
        )
    )
    ctx = _ctx(adapter, scripted, session_store)

    reply = handle_turn(ctx, "s12c", "the jacket one, is it 100% cotton like the other item")

    assert "Blue Jacket" in reply
    assert "?" not in reply or "did you mean" not in reply.lower()


def test_get_product_details_never_fabricates_an_answer_with_nothing_to_back_it(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    scripted = _ScriptedLLMClient(
        ActionCall(
            action_type="get_product_details",
            parameters={"raw_text": "what sizes does the completely nonexistent thing have"},
        )
    )
    ctx = _ctx(adapter, scripted, session_store)

    reply = handle_turn(ctx, "s13", "what sizes does the completely nonexistent thing have")

    assert "couldn't find" in reply.lower()


# -- Per-turn product/cart links (api/chat.py's ChatResponse.product_links/show_cart_link) #


def test_single_result_search_sets_auto_navigate_not_a_link(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    handle_turn(_ctx(adapter, llm_client, session_store), "s14", "show me jackets")

    session = session_store.get_or_create("s14")
    # Exactly one result — unambiguous enough to auto-navigate to; a link would be redundant.
    assert session.last_turn_auto_navigate_product_id == "prod-jacket-1"
    assert session.last_turn_product_ids == []
    assert session.last_turn_product_names == []
    assert session.last_turn_shows_cart_link is False


def test_multi_result_search_sets_links_not_auto_navigate(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    """Multiple results (both catalog products) are never auto-navigate-worthy — there's no
    single unambiguous target to redirect to; links only."""
    scripted = _ScriptedLLMClient(ActionCall(action_type="search_products", parameters={"query": ""}))
    ctx = _ctx(adapter, scripted, session_store)

    handle_turn(ctx, "s16", "show me everything")

    session = session_store.get_or_create("s16")
    assert len(session.last_turn_product_ids) > 1
    assert session.last_turn_auto_navigate_product_id is None


def test_auto_navigate_target_does_not_linger_into_an_unrelated_later_turn(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    ctx = _ctx(adapter, RuleBasedStubClient(), session_store)
    handle_turn(ctx, "s15", "show me jackets")
    assert session_store.get_or_create("s15").last_turn_auto_navigate_product_id == "prod-jacket-1"

    scripted = _ScriptedLLMClient(ActionCall(action_type="ask_or_chat", parameters={"text": "Sure!"}))
    ctx2 = _ctx(adapter, scripted, session_store)
    handle_turn(ctx2, "s15", "thanks")

    session = session_store.get_or_create("s15")
    assert session.last_turn_auto_navigate_product_id is None


def test_ambiguous_get_product_details_reply_is_never_handed_to_phrase_reply(
    adapter: MockAdapter, session_store: SessionStore
) -> None:
    # Two real, meaningful tokens ("shirt", "jacket") each keyword-match a DIFFERENT one of
    # MockAdapter's two products — genuinely ambiguous, unlike an empty/too-short term (which
    # a real bug used to let fall through to "browse the whole catalog" instead of the
    # intended "nothing meaningful to search on" — see agent/intents.py's
    # _resolve_single_product and its regression tests in test_us2_cart.py).
    scripted = _LyingScriptedLLMClient(
        ActionCall(action_type="get_product_details", parameters={"raw_text": "shirt jacket"})
    )
    ctx = _ctx(adapter, scripted, session_store)

    reply = handle_turn(ctx, "s17", "shirt jacket")

    assert reply != "Got it — here's the exact item you asked about."
    assert "did you mean" in reply.lower()
    session = session_store.get_or_create("s17")
    assert session.last_turn_auto_navigate_product_id is None  # genuinely unresolved
