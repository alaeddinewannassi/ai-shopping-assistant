"""Session list + replay endpoints (T506) — the admin-facing upgrade of chatbot/backend's
GET /audit/{session_id}, reading the same assistant_event/conversation_session tables."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from tenancy_db.models.analytics import ConversationSessionRecord
from tenancy_db.repositories import AssistantEventRepository

from src.auth.dependencies import VIEW_SESSIONS, get_db, require_tenant_role

router = APIRouter(prefix="/tenants/{tenant_id}/sessions", tags=["sessions"])


class SessionSummaryOut(BaseModel):
    session_id: str
    started_at: str
    last_seen_at: str
    turn_count: int
    outcome: str
    cart_id: str | None
    order_id: str | None


class AssistantEventOut(BaseModel):
    turn_id: str
    seq: int
    occurred_at: str
    intent: str
    action: str
    outcome: str
    details: dict
    turn_elapsed_ms: int | None


@router.get("", response_model=list[SessionSummaryOut])
def list_sessions(
    tenant_id: uuid.UUID,
    limit: int = Query(default=50, le=200),
    outcome: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin=Depends(require_tenant_role(*VIEW_SESSIONS)),
) -> list[SessionSummaryOut]:
    stmt = (
        sa.select(ConversationSessionRecord)
        .where(ConversationSessionRecord.tenant_id == tenant_id)
        .order_by(ConversationSessionRecord.last_seen_at.desc())
        .limit(limit)
    )
    if outcome is not None:
        stmt = stmt.where(ConversationSessionRecord.outcome == outcome)
    rows = db.scalars(stmt).all()
    return [
        SessionSummaryOut(
            session_id=r.session_id,
            started_at=r.started_at.isoformat(),
            last_seen_at=r.last_seen_at.isoformat(),
            turn_count=r.turn_count,
            outcome=r.outcome,
            cart_id=r.cart_id,
            order_id=r.order_id,
        )
        for r in rows
    ]


@router.get("/{session_id}/events", response_model=list[AssistantEventOut])
def get_session_events(
    tenant_id: uuid.UUID,
    session_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_tenant_role(*VIEW_SESSIONS)),
) -> list[AssistantEventOut]:
    events = AssistantEventRepository(db).list_for_session(tenant_id, session_id)
    return [
        AssistantEventOut(
            turn_id=str(e.turn_id),
            seq=e.seq,
            occurred_at=e.occurred_at.isoformat(),
            intent=e.intent,
            action=e.action,
            outcome=e.outcome,
            details=e.details,
            turn_elapsed_ms=e.turn_elapsed_ms,
        )
        for e in events
    ]
