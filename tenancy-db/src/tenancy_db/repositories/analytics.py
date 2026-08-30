"""Repositories for the assistant event stream + per-session summary (T301/T304/T309).

`AssistantEventRepository.insert_many` is written for the batch writer's shape (a list of
plain dicts accumulated off the hot path, flushed periodically) rather than one row at a
time — see chatbot/backend/src/logging/audit.py.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from tenancy_db.base import utcnow
from tenancy_db.models.analytics import AssistantEvent, ConversationSessionRecord


class AssistantEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_many(self, rows: list[dict]) -> None:
        """`rows` are plain dicts matching AssistantEvent's columns (event_id/tenant_id/
        session_id/turn_id/seq/occurred_at/intent/action/outcome/details/turn_elapsed_ms).
        A no-op on an empty list — callers don't need to special-case that themselves."""
        if not rows:
            return
        self._session.execute(sa.insert(AssistantEvent), rows)
        self._session.flush()

    def list_for_session(self, tenant_id: uuid.UUID, session_id: str) -> list[AssistantEvent]:
        stmt = (
            sa.select(AssistantEvent)
            .where(AssistantEvent.tenant_id == tenant_id, AssistantEvent.session_id == session_id)
            .order_by(AssistantEvent.turn_id, AssistantEvent.seq)
        )
        return list(self._session.scalars(stmt).all())


class ConversationSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_turn(
        self,
        tenant_id: uuid.UUID,
        session_id: str,
        *,
        cart_id: str | None = None,
        order_id: str | None = None,
        outcome: str | None = None,
    ) -> ConversationSessionRecord:
        """Bumps turn_count/last_seen_at for this (tenant, session), creating the row on
        first turn. `outcome` only ever moves forward (browsing -> cart -> ordered) — a
        later turn must never downgrade an already-classified session."""
        stmt = sa.select(ConversationSessionRecord).where(
            ConversationSessionRecord.tenant_id == tenant_id,
            ConversationSessionRecord.session_id == session_id,
        )
        record = self._session.scalars(stmt).first()
        now = utcnow()
        if record is None:
            record = ConversationSessionRecord(
                tenant_id=tenant_id,
                session_id=session_id,
                last_seen_at=now,
                turn_count=1,
                outcome=outcome or "browsing",
                cart_id=cart_id,
                order_id=order_id,
            )
            self._session.add(record)
        else:
            record.last_seen_at = now
            record.turn_count += 1
            if cart_id is not None:
                record.cart_id = cart_id
            if order_id is not None:
                record.order_id = order_id
            if outcome is not None and _OUTCOME_RANK[outcome] > _OUTCOME_RANK[record.outcome]:
                record.outcome = outcome
        self._session.flush()
        return record


_OUTCOME_RANK = {"browsing": 0, "cart": 1, "ordered": 2, "abandoned": 1}
