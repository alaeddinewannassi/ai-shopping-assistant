"""AssistantEventRepository / ConversationSessionRepository (T301/T309).

Not a duplicate of test_tenant_isolation.py's config-table isolation tests — this covers
the two behaviors specific to the event stream: batch insert (the writer's actual usage
shape) and outcome ranking never regressing (a later "browsing" turn must not downgrade an
already-"ordered" session).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from tenancy_db.base import Base
from tenancy_db.repositories import (
    AssistantEventRepository,
    ConversationSessionRepository,
    TenantRepository,
)


@pytest.fixture
def session():
    engine = sa.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    yield db
    db.close()
    engine.dispose()


@pytest.fixture
def tenant_id(session):
    tenant = TenantRepository(session).create("store-a", "Store A")
    session.commit()
    return tenant.id


def test_insert_many_is_a_noop_on_empty_list(session, tenant_id) -> None:
    AssistantEventRepository(session).insert_many([])  # must not raise


def test_insert_many_batches_rows_for_one_turn(session, tenant_id) -> None:
    turn_id = uuid.uuid4()
    now = datetime.now(UTC)
    rows = [
        {
            "event_id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "session_id": "s1",
            "turn_id": turn_id,
            "seq": i,
            "occurred_at": now,
            "intent": "propose_add_to_cart",
            "action": "propose",
            "outcome": "pending",
            "details": {},
            "turn_elapsed_ms": 42 if i == 1 else None,
        }
        for i in range(2)
    ]
    AssistantEventRepository(session).insert_many(rows)
    session.commit()

    events = AssistantEventRepository(session).list_for_session(tenant_id, "s1")
    assert [e.seq for e in events] == [0, 1]
    assert events[1].turn_elapsed_ms == 42


def test_list_for_session_orders_by_when_things_actually_happened_not_turn_id(session, tenant_id) -> None:
    """Regression test for a real, confirmed live bug: an admin reading a session's event
    log saw timestamps jump around (2:47:21 -> 2:47:19 -> 2:47:36 -> 2:47:34) instead of
    reading top-to-bottom in order. Root cause: this used to order by (turn_id, seq), and
    turn_id is a plain random UUID4 (agent/turn_context.py's TurnContext) — unrelated to
    when a turn actually happened. Three turns inserted in a deliberately scrambled order
    (by turn_id) must still come back sorted by their real occurred_at."""
    from datetime import timedelta

    turn_a, turn_b, turn_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 9, 13, 14, 47, 0, tzinfo=UTC)
    rows = [
        # Inserted out of chronological order — turn_b (middle in time) first, to prove
        # the ordering comes from occurred_at, not insertion order or turn_id sort order.
        {
            "event_id": uuid.uuid4(), "tenant_id": tenant_id, "session_id": "s1",
            "turn_id": turn_b, "seq": 0, "occurred_at": base + timedelta(seconds=34),
            "intent": "propose_add_to_cart", "action": "propose", "outcome": "pending",
            "details": {}, "turn_elapsed_ms": None,
        },
        {
            "event_id": uuid.uuid4(), "tenant_id": tenant_id, "session_id": "s1",
            "turn_id": turn_a, "seq": 0, "occurred_at": base + timedelta(seconds=19),
            "intent": "search_products", "action": "search_products", "outcome": "products",
            "details": {}, "turn_elapsed_ms": None,
        },
        {
            "event_id": uuid.uuid4(), "tenant_id": tenant_id, "session_id": "s1",
            "turn_id": turn_c, "seq": 0, "occurred_at": base + timedelta(seconds=36),
            "intent": "confirm_pending_action", "action": "confirm", "outcome": "success",
            "details": {}, "turn_elapsed_ms": None,
        },
    ]
    AssistantEventRepository(session).insert_many(rows)
    session.commit()

    events = AssistantEventRepository(session).list_for_session(tenant_id, "s1")

    assert [e.turn_id for e in events] == [turn_a, turn_b, turn_c]
    assert [e.occurred_at for e in events] == sorted(e.occurred_at for e in events)


def test_conversation_session_outcome_never_downgrades(session, tenant_id) -> None:
    repo = ConversationSessionRepository(session)
    repo.upsert_turn(tenant_id, "s1", outcome="cart", cart_id="cart-1")
    session.commit()
    record = repo.upsert_turn(tenant_id, "s1", outcome="ordered", order_id="order-1")
    session.commit()
    assert record.outcome == "ordered"

    # A later, unrelated turn with no outcome hint must not reset it.
    record = repo.upsert_turn(tenant_id, "s1")
    session.commit()
    assert record.outcome == "ordered"
    assert record.turn_count == 3
    assert record.cart_id == "cart-1"
    assert record.order_id == "order-1"


def test_checkout_outcome_sits_between_cart_and_ordered(session, tenant_id) -> None:
    """"checkout" (a session handed off to native checkout — see chatbot/backend's
    _upsert_conversation_session) is real purchase INTENT, a step further than a cart
    mutation but not a confirmed order — must rank strictly between "cart" and "ordered" so
    it upgrades a merely-browsing/cart session but never downgrades an already-"ordered"
    one, and is itself never downgraded by a later plain cart mutation."""
    repo = ConversationSessionRepository(session)
    repo.upsert_turn(tenant_id, "s2", outcome="cart")
    session.commit()

    record = repo.upsert_turn(tenant_id, "s2", outcome="checkout")
    session.commit()
    assert record.outcome == "checkout"

    # A later cart mutation in the same session (e.g. the shopper went back and changed
    # quantity) must not downgrade a session that already reached checkout.
    record = repo.upsert_turn(tenant_id, "s2", outcome="cart")
    session.commit()
    assert record.outcome == "checkout"

    record = repo.upsert_turn(tenant_id, "s2", outcome="ordered")
    session.commit()
    assert record.outcome == "ordered"


def test_events_and_sessions_are_scoped_per_tenant(session) -> None:
    tenants = TenantRepository(session)
    tenant_a = tenants.create("store-a", "Store A")
    tenant_b = tenants.create("store-b", "Store B")
    session.commit()

    events = AssistantEventRepository(session)
    events.insert_many(
        [
            {
                "event_id": uuid.uuid4(),
                "tenant_id": tenant_a.id,
                "session_id": "shared-id",
                "turn_id": uuid.uuid4(),
                "seq": 0,
                "occurred_at": datetime.now(UTC),
                "intent": "search_products",
                "action": "search_products",
                "outcome": "products",
                "details": {},
                "turn_elapsed_ms": None,
            }
        ]
    )
    session.commit()

    assert len(events.list_for_session(tenant_a.id, "shared-id")) == 1
    assert len(events.list_for_session(tenant_b.id, "shared-id")) == 0
