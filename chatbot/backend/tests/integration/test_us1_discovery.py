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
