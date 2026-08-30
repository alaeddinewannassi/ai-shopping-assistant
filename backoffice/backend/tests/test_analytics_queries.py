"""Synthetic event fixtures -> asserted overview + funnel numbers (T406).

"Correctness of the numbers is the product" — this test constructs a small, fully-known
event set by hand (not by simulating a real conversation) so every expected count/latency
value is independently computable and checked exactly, rather than just "did it run."
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from tenancy_db.base import Base
from tenancy_db.models.analytics import AssistantEvent, ConversationSessionRecord

from src.analytics.queries import get_funnel, get_overview

_NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db():
    engine = sa.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _event(
    tenant_id, session_id, turn_id, seq, intent, action, outcome, *, details=None, elapsed_ms=None
):
    return AssistantEvent(
        event_id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        occurred_at=_NOW,
        intent=intent,
        action=action,
        outcome=outcome,
        details=details or {},
        turn_elapsed_ms=elapsed_ms,
    )


def test_overview_and_funnel_numbers_match_hand_computed_expectations(db) -> None:
    tenant_id = uuid.uuid4()
    other_tenant_id = uuid.uuid4()

    # Session 1: full funnel — discovery, add-to-cart proposed+confirmed, checkout
    # proposed+confirmed -> ordered.
    t1a, t1b, t1c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    events = [
        _event(tenant_id, "s1", t1a, 0, "search_products", "search_products", "products"),
        _event(tenant_id, "s1", t1a, 1, "turn_completed", "turn_completed", "ok", elapsed_ms=100),
        _event(tenant_id, "s1", t1b, 0, "propose_add_to_cart", "propose", "pending"),
        _event(
            tenant_id, "s1", t1b, 1, "confirm_pending_action", "confirm", "success",
            details={"action_type": "add_cart_item"},
        ),
        _event(tenant_id, "s1", t1b, 2, "turn_completed", "turn_completed", "ok", elapsed_ms=200),
        _event(tenant_id, "s1", t1c, 0, "request_checkout", "propose", "pending"),
        _event(
            tenant_id, "s1", t1c, 1, "confirm_pending_action", "confirm", "success",
            details={"action_type": "checkout"},
        ),
        _event(tenant_id, "s1", t1c, 2, "turn_completed", "turn_completed", "ok", elapsed_ms=300),
    ]

    # Session 2: discovery only, never proposes anything.
    t2 = uuid.uuid4()
    events += [
        _event(tenant_id, "s2", t2, 0, "search_products", "search_products", "no_match"),
        _event(tenant_id, "s2", t2, 1, "turn_completed", "turn_completed", "ok", elapsed_ms=50),
    ]

    # Session 3: discovery + a proposal that's never confirmed, plus one store outage.
    t3a, t3b = uuid.uuid4(), uuid.uuid4()
    events += [
        _event(tenant_id, "s3", t3a, 0, "search_products", "search_products", "products"),
        _event(tenant_id, "s3", t3a, 1, "turn_completed", "turn_completed", "ok", elapsed_ms=150),
        _event(tenant_id, "s3", t3b, 0, "propose_add_to_cart", "propose", "pending"),
        _event(tenant_id, "s3", t3b, 1, "search_products", "search_products", "unavailable"),
        _event(tenant_id, "s3", t3b, 2, "turn_completed", "turn_completed", "ok", elapsed_ms=75),
    ]

    # A different tenant's events must never leak into tenant_id's numbers.
    t_other = uuid.uuid4()
    events.append(
        _event(other_tenant_id, "s1", t_other, 0, "search_products", "search_products", "products")
    )

    db.add_all(events)
    db.add_all(
        [
            ConversationSessionRecord(
                tenant_id=tenant_id, session_id="s1", last_seen_at=_NOW, turn_count=3, outcome="ordered"
            ),
            ConversationSessionRecord(
                tenant_id=tenant_id, session_id="s2", last_seen_at=_NOW, turn_count=1, outcome="browsing"
            ),
            ConversationSessionRecord(
                tenant_id=tenant_id, session_id="s3", last_seen_at=_NOW, turn_count=2, outcome="browsing"
            ),
        ]
    )
    db.commit()

    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    funnel = get_funnel(db, tenant_id, start, end)
    assert funnel.sessions == 3
    assert funnel.discovery == 3  # s1, s2, s3 all searched
    assert funnel.proposal == 2  # s1 (add + checkout), s3 (add) — s2 never proposed
    assert funnel.confirmed == 1  # only s1 confirmed anything
    assert funnel.cart_mutated == 1  # s1's add_cart_item confirm
    assert funnel.checkout_proposed == 1  # only s1 proposed checkout
    assert funnel.ordered == 1  # only s1

    overview = get_overview(db, tenant_id, start, end)
    assert overview.session_count == 3
    # 3 turns in s1 (search, add-to-cart, checkout) + 2 in s3 (search, add-to-cart-attempt) + 1 in s2
    assert overview.turn_count == 6
    assert overview.ordered_session_count == 1
    assert overview.conversion_rate == pytest.approx(1 / 3)
    assert overview.avg_turn_latency_ms == pytest.approx((100 + 200 + 300 + 50 + 150 + 75) / 6)
    assert overview.error_event_count == 1  # s3's "unavailable" search
    non_turn_total = sum(1 for e in events if e.tenant_id == tenant_id and e.intent != "turn_completed")
    assert overview.error_rate == pytest.approx(1 / non_turn_total)


def test_empty_range_returns_zeroed_metrics_not_a_crash(db) -> None:
    tenant_id = uuid.uuid4()
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    funnel = get_funnel(db, tenant_id, start, end)
    assert funnel.sessions == 0
    assert funnel.ordered == 0

    overview = get_overview(db, tenant_id, start, end)
    assert overview.session_count == 0
    assert overview.conversion_rate == 0.0
    assert overview.avg_turn_latency_ms is None
    assert overview.error_rate == 0.0
