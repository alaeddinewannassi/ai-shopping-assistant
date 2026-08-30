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
