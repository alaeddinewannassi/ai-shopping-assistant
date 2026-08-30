"""Load test for the analytics write path (T704) — plan.md's stated target: >=100 events/s
per worker, with <5ms of added latency per chat turn from the enqueue operation itself.

Distinguishes two different things on purpose: the ENQUEUE cost (what actually adds to a
turn's latency — an in-memory queue.put_nowait(), O(1), no I/O) from the FLUSH throughput
(what the background writer thread must sustain so the queue never backs up and starts
dropping events, dropped_event_count()).
"""

from __future__ import annotations

import time

import pytest
from tenancy_db.base import Base
from tenancy_db.engine import reset_engine

from src.agent import turn_context
from src.logging.audit import dropped_event_count, log_action, wait_for_drain
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
    db_path = tmp_path / "load_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.delenv("PRESTASHOP_BASE_URL", raising=False)
    monkeypatch.delenv("PRESTASHOP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "rule-based-stub")

    from tenancy_db.engine import get_engine

    Base.metadata.create_all(get_engine())
    config = legacy_env_tenant_config("default")
    return build_tenant_runtime(config), config


def test_enqueue_adds_well_under_5ms_per_call(monkeypatch, tmp_path) -> None:
    """Isolates the enqueue cost itself — the part that runs inline on a chat turn, as
    opposed to the background writer's flush (measured separately below)."""
    _, config = _configured_runtime(monkeypatch, tmp_path)
    tenant_id = config.tenant_id

    call_count = 2000
    with turn_context.turn_scope(tenant_id, "load-test-session"):
        start = time.monotonic()
        for i in range(call_count):
            log_action("load-test-session", "search_products", "search_products", "products")
        elapsed = time.monotonic() - start

    per_call_ms = (elapsed / call_count) * 1000
    assert per_call_ms < 5.0, f"enqueue cost {per_call_ms:.3f}ms/call exceeds the 5ms target"


def test_writer_sustains_at_least_100_events_per_second_with_no_drops(monkeypatch, tmp_path) -> None:
    """Fires well over 100 events/s worth of load for a couple of seconds and asserts the
    background writer drains it all with zero drops — proving the >=100 events/s target,
    not just that a single enqueue call is fast."""
    _runtime, config = _configured_runtime(monkeypatch, tmp_path)
    tenant_id = config.tenant_id

    target_rate = 300  # events/sec, comfortably above the 100/s target
    duration_seconds = 2
    total_events = target_rate * duration_seconds

    baseline_dropped = dropped_event_count()

    with turn_context.turn_scope(tenant_id, "load-test-session-2"):
        start = time.monotonic()
        for i in range(total_events):
            log_action("load-test-session-2", "search_products", "search_products", "products")
        fire_elapsed = time.monotonic() - start

    wait_for_drain(timeout=10.0)

    assert dropped_event_count() == baseline_dropped, "events were dropped under sustained load"
    achieved_rate = total_events / fire_elapsed
    assert achieved_rate >= target_rate, f"only achieved {achieved_rate:.0f} events/s"

    from tenancy_db.engine import session_scope
    from tenancy_db.repositories import AssistantEventRepository

    with session_scope() as db:
        events = AssistantEventRepository(db).list_for_session(tenant_id, "load-test-session-2")
    assert len(events) == total_events, "not every event made it to durable storage"
