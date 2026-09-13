"""Regression tests for T066's audit-logging completeness review (FR-014).

Targets the two gaps the review found: an unavailable-store refusal during a cart-line
update/remove proposal was silently unlogged (inconsistent with the equivalent add-to-cart
branch), and a decline logged only "something was declined", not *what*.
"""

from __future__ import annotations

import json
import logging

import pytest

from src.adapters.mock import MockAdapter
from src.agent import turn_context
from src.agent.dialogue import DialogueContext, handle_turn
from src.agent.intents import CartIntentHandler, DiscoveryIntentHandler
from src.agent.llm_client import RuleBasedStubClient
from src.agent.pending import PendingActionGate
from src.agent.taxonomy_resolver import TaxonomyResolver
from src.logging.audit import log_turn_completed
from src.session.catalog_cache import CatalogSnapshotCache
from src.session.store import SessionStore


@pytest.fixture
def ctx() -> DialogueContext:
    adapter = MockAdapter()
    session_store = SessionStore(redis_url=None)
    resolver = TaxonomyResolver(adapter)
    return DialogueContext(
        session_store=session_store,
        llm_client=RuleBasedStubClient(),
        discovery_handler=DiscoveryIntentHandler(adapter, resolver, CatalogSnapshotCache()),
        adapter=adapter,
        cart_handler=CartIntentHandler(adapter),
        pending_gate=PendingActionGate(session_store, adapter),
    )


def _records(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [json.loads(r.message) for r in caplog.records]


def test_turn_completed_carries_live_ratelimit_headroom_when_a_real_llm_call_made_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Backs the backoffice's LLM capacity gauge: turn_context.py's record_llm_usage captures
    Groq's live rate-limit headers, and this is the one place that reads them back onto the
    durably-stored turn_completed event (details is already free-form JSON — no new column
    needed, since this is a point-in-time snapshot, not something to sum across rows)."""
    caplog.set_level(logging.INFO, logger="assistant.audit")
    with turn_context.turn_scope(None, "s1") as turn:
        turn.record_llm_usage(
            provider="free-tier-hosted",
            model="openai/gpt-oss-120b",
            prompt_tokens=50,
            completion_tokens=10,
            llm_ms=200,
            ratelimit_limit_requests=1000,
            ratelimit_remaining_requests=993,
            ratelimit_limit_tokens=8000,
            ratelimit_remaining_tokens=7927,
        )
        log_turn_completed("s1")

    record = _records(caplog)[0]
    assert record["details"]["ratelimit_remaining_requests"] == 993
    assert record["details"]["ratelimit_limit_requests"] == 1000
    assert record["details"]["ratelimit_remaining_tokens"] == 7927
    assert record["details"]["ratelimit_limit_tokens"] == 8000


def test_turn_completed_omits_ratelimit_fields_when_no_real_llm_call_was_made(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RuleBasedStubClient never calls record_llm_usage — every existing turn_completed
    event's details shape must stay exactly as-is (no null ratelimit_* clutter)."""
    caplog.set_level(logging.INFO, logger="assistant.audit")
    with turn_context.turn_scope(None, "s1"):
        log_turn_completed("s1")

    record = _records(caplog)[0]
    assert "ratelimit_remaining_requests" not in record["details"]


def test_unavailable_store_during_update_proposal_is_logged(
    ctx: DialogueContext, caplog: pytest.LogCaptureFixture
) -> None:
    handle_turn(ctx, "audit-1", "add the red classic t-shirt to my cart")
    handle_turn(ctx, "audit-1", "yes")

    ctx.adapter.simulate_outage(True)
    with caplog.at_level(logging.INFO, logger="assistant.audit"):
        handle_turn(ctx, "audit-1", "update the classic t-shirt quantity to 3")

    records = _records(caplog)
    assert any(r["intent"] == "propose_update_cart" and r["outcome"] == "unavailable" for r in records)


def test_unavailable_store_during_remove_proposal_is_logged(
    ctx: DialogueContext, caplog: pytest.LogCaptureFixture
) -> None:
    handle_turn(ctx, "audit-2", "add the red classic t-shirt to my cart")
    handle_turn(ctx, "audit-2", "yes")

    ctx.adapter.simulate_outage(True)
    with caplog.at_level(logging.INFO, logger="assistant.audit"):
        handle_turn(ctx, "audit-2", "remove the classic t-shirt")

    records = _records(caplog)
    assert any(r["intent"] == "propose_remove_from_cart" and r["outcome"] == "unavailable" for r in records)


def test_decline_logs_which_action_type_was_declined(
    ctx: DialogueContext, caplog: pytest.LogCaptureFixture
) -> None:
    handle_turn(ctx, "audit-3", "add the red classic t-shirt to my cart")

    with caplog.at_level(logging.INFO, logger="assistant.audit"):
        handle_turn(ctx, "audit-3", "no, cancel that")

    records = _records(caplog)
    decline_records = [r for r in records if r["intent"] == "decline_pending_action"]
    assert len(decline_records) == 1
    assert decline_records[0]["details"]["declined_action_type"] == "add_cart_item"
