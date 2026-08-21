"""Integration tests for User Story 1 - Conversational Product Discovery & Navigation
(T018-T021a). Exercises the full stack: RuleBasedStubClient -> DiscoveryIntentHandler ->
TaxonomyResolver -> MockAdapter -> dialogue.handle_turn, the same wiring api/chat.py uses.
"""

from __future__ import annotations

import pytest

from src.adapters.base import AttributeGroup, Category
from src.adapters.mock import MockAdapter
from src.agent.dialogue import handle_turn
from src.agent.intents import DiscoveryIntentHandler
from src.agent.llm_client import RuleBasedStubClient
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


def _discovery_handler(adapter: MockAdapter) -> DiscoveryIntentHandler:
    resolver = TaxonomyResolver(adapter)
    return DiscoveryIntentHandler(adapter, resolver, CatalogSnapshotCache())


# -- Scenario 1: category + price constraint search --------------------------- #


def test_search_by_category_and_price_constraint_returns_matching_products(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(
        session_store, llm_client, _discovery_handler(adapter), "s1", "show me jackets under $100"
    )
    assert "Blue Jacket" in reply


def test_search_by_category_and_price_constraint_excludes_out_of_range_products(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(
        session_store, llm_client, _discovery_handler(adapter), "s1", "show me jackets under $50"
    )
    assert "Blue Jacket" not in reply
    assert "couldn't find" in reply


# -- Scenario 2: navigate to a named category/product ------------------------- #


def test_navigate_to_named_category(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(
        session_store, llm_client, _discovery_handler(adapter), "s2", "take me to the t-shirts category"
    )
    assert "T-Shirts" in reply or "t-shirts" in reply.lower()
    assert "Classic T-Shirt" in reply

    session = session_store.get_or_create("s2")
    assert session.navigation_context.get("category_id") == "cat-tshirts"


def test_navigate_to_named_product(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(
        session_store, llm_client, _discovery_handler(adapter), "s3", "go to Blue Jacket"
    )
    assert "Blue Jacket" in reply


# -- Scenario 3: ambiguous request -> exactly one clarifying question --------- #


def test_ambiguous_category_term_triggers_one_clarifying_question(
    llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    adapter = MockAdapter()
    # Seed a second, deliberately overlapping category so "jacket" is genuinely ambiguous
    # between two real categories — exercising ResolutionStatus.AMBIGUOUS end-to-end.
    adapter._categories["cat-rain-jackets"] = Category(id="cat-rain-jackets", name="Rain Jackets")

    reply = handle_turn(
        session_store, llm_client, _discovery_handler(adapter), "s4", "show me jackets"
    )
    assert "?" in reply
    assert reply.count("?") == 1  # at most one clarifying question (FR-003)
    assert "Jackets" in reply and "Rain Jackets" in reply


# -- Scenario 4: no catalog matches -> plain message, no dead-end navigation --- #


def test_no_matches_returns_plain_message_not_dead_end(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    reply = handle_turn(
        session_store, llm_client, _discovery_handler(adapter), "s5", "show me nonexistent gizmos"
    )
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
    handler = _discovery_handler(adapter)

    # Warm the cache with a successful live search first.
    warm_up = handle_turn(session_store, llm_client, handler, "s6", "show me jackets")
    assert "Blue Jacket" in warm_up

    adapter.simulate_outage(True)
    reply = handle_turn(session_store, llm_client, handler, "s6", "show me jackets")

    assert "may be outdated" in reply.lower() or "cached" in reply.lower()
    assert "Blue Jacket" in reply


def test_backend_unreachable_with_nothing_cached_gives_plain_unavailable_message(
    adapter: MockAdapter, llm_client: RuleBasedStubClient, session_store: SessionStore
) -> None:
    handler = _discovery_handler(adapter)
    adapter.simulate_outage(True)

    reply = handle_turn(session_store, llm_client, handler, "s7", "show me completely new query xyz")

    assert "can't reach" in reply.lower() or "can't search" in reply.lower()
    assert "Jacket" not in reply and "T-Shirt" not in reply  # never fabricate product data
