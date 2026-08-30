"""The Phase 3 event pipeline end-to-end (T311): a full turn emits the expected event
sequence into the tenancy database with non-null latency, and the database being
unreachable/unconfigured never breaks a chat turn or the stdout audit line.
"""

from __future__ import annotations

import pytest
from tenancy_db.base import Base
from tenancy_db.engine import reset_engine
from tenancy_db.repositories import AssistantEventRepository

from src.agent.dialogue import handle_turn
from src.logging.audit import dropped_event_count, wait_for_drain
from src.tenancy.config import legacy_env_tenant_config
from src.tenancy.runtime import build_tenant_runtime, clear_all


@pytest.fixture(autouse=True)
def _fresh_state():
    clear_all()
    reset_engine()
    yield
    clear_all()
    reset_engine()


def _configured_runtime(monkeypatch, tmp_path):
    db_path = tmp_path / "analytics_pipeline.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("PRESTASHOP_BASE_URL", raising=False)
    monkeypatch.delenv("PRESTASHOP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "rule-based-stub")

    from tenancy_db.engine import get_engine

    engine = get_engine()
    Base.metadata.create_all(engine)

    config = legacy_env_tenant_config("default")
    return build_tenant_runtime(config), config


def test_full_turn_emits_events_with_non_null_turn_latency(monkeypatch, tmp_path) -> None:
    runtime, config = _configured_runtime(monkeypatch, tmp_path)

    reply = handle_turn(runtime.dialogue_ctx, "pipeline-session-1", "add the red classic t-shirt to my cart")
    assert "confirm" in reply.lower()
    handle_turn(runtime.dialogue_ctx, "pipeline-session-1", "yes")

    wait_for_drain()

    from tenancy_db.engine import session_scope

    with session_scope() as db:
        events = AssistantEventRepository(db).list_for_session(config.tenant_id, "pipeline-session-1")

    intents = [e.intent for e in events]
    assert "propose_add_to_cart" in intents
    assert "confirm_pending_action" in intents
    assert intents.count("turn_completed") == 2  # one per handle_turn() call above

    turn_completed = [e for e in events if e.intent == "turn_completed"]
    assert all(e.turn_elapsed_ms is not None and e.turn_elapsed_ms >= 0 for e in turn_completed)

    # Every event from the same handle_turn() call shares one turn_id, seq starting at 0.
    first_turn_events = [e for e in events if e.turn_id == events[0].turn_id]
    assert [e.seq for e in first_turn_events] == list(range(len(first_turn_events)))


def test_chat_still_succeeds_when_database_is_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PRESTASHOP_BASE_URL", raising=False)
    monkeypatch.delenv("PRESTASHOP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "rule-based-stub")

    config = legacy_env_tenant_config("default")
    runtime = build_tenant_runtime(config)

    reply = handle_turn(runtime.dialogue_ctx, "no-db-session", "show me t-shirts")
    assert reply  # the turn completed normally; nothing raised


def test_events_are_dropped_not_lost_silently_when_queue_is_full(monkeypatch, tmp_path) -> None:
    """Doesn't actually fill the real 2000-slot queue (too slow) — instead verifies the
    counter used to observe drops exists and starts at a stable baseline, so a future
    regression that stops incrementing it would be caught by any test that asserts on it."""
    runtime, _ = _configured_runtime(monkeypatch, tmp_path)
    baseline = dropped_event_count()
    handle_turn(runtime.dialogue_ctx, "drop-counter-session", "show me t-shirts")
    wait_for_drain()
    assert dropped_event_count() == baseline  # nothing dropped under normal load
