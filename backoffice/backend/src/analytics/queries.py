"""Dashboard query layer (T403/T404) — reads only, over the raw assistant_event /
conversation_session tables chatbot/backend writes (T301-T310).

Scoped down from the original plan: no rollup-table routing yet (T401's analytics_hourly/
analytics_daily tables and T402's scheduler aren't built — see
specs/002-backoffice-analytics/plan.md's Phase 4 status). Every function here scans raw
events directly, which is always correct and simple, just not yet optimized for large date
ranges — the honest, testable building block D5's rollup-vs-raw routing would eventually
sit in front of.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session
from tenancy_db.models.analytics import AssistantEvent, ConversationSessionRecord

# The only outcome string that means "the store/adapter genuinely couldn't be reached" in
# today's vocabulary (chatbot/backend's agent/dialogue.py) — out_of_stock/declined/
# cart_state_changed are legitimate business outcomes, not errors.
_ERROR_OUTCOMES = {"unavailable"}

_MUTATION_ACTION_TYPES = {"add_cart_item", "update_cart_item", "remove_cart_item", "apply_promo"}


@dataclass
class OverviewMetrics:
    session_count: int
    turn_count: int
    ordered_session_count: int
    conversion_rate: float  # ordered_session_count / session_count, 0.0 if no sessions
    avg_turn_latency_ms: float | None
    p95_turn_latency_ms: float | None
    error_event_count: int
    error_rate: float  # error_event_count / non-turn_completed events, 0.0 if none


@dataclass
class FunnelMetrics:
    sessions: int
    discovery: int
    proposal: int
    confirmed: int
    cart_mutated: int
    checkout_proposed: int
    ordered: int


def get_overview(db: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> OverviewMetrics:
    """Overview panel: activity, conversion, latency, error rate for `[start, end)`."""
    events = _events_in_range(db, tenant_id, start, end)
    session_ids = {e.session_id for e in events}
    turn_events = [e for e in events if e.intent == "turn_completed"]
    latencies = [e.turn_elapsed_ms for e in turn_events if e.turn_elapsed_ms is not None]
    non_turn_events = [e for e in events if e.intent != "turn_completed"]
    error_count = sum(1 for e in non_turn_events if e.outcome in _ERROR_OUTCOMES)

    ordered_count = _count_sessions_with_outcome(db, tenant_id, session_ids, "ordered")

    return OverviewMetrics(
        session_count=len(session_ids),
        turn_count=len(turn_events),
        ordered_session_count=ordered_count,
        conversion_rate=(ordered_count / len(session_ids)) if session_ids else 0.0,
        avg_turn_latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
        p95_turn_latency_ms=_percentile(latencies, 0.95) if latencies else None,
        error_event_count=error_count,
        error_rate=(error_count / len(non_turn_events)) if non_turn_events else 0.0,
    )


def get_funnel(db: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> FunnelMetrics:
    """Funnel panel: sessions -> discovery -> proposal -> confirmed -> cart_mutated ->
    checkout_proposed -> ordered, each a DISTINCT session count (a session can land in
    multiple stages — that's the point of a funnel, not a bug)."""
    events = _events_in_range(db, tenant_id, start, end)
    by_session: dict[str, list[AssistantEvent]] = {}
    for e in events:
        by_session.setdefault(e.session_id, []).append(e)

    discovery: set[str] = set()
    proposal: set[str] = set()
    confirmed: set[str] = set()
    cart_mutated: set[str] = set()
    checkout_proposed: set[str] = set()

    for session_id, session_events in by_session.items():
        for e in session_events:
            if e.intent in ("search_products", "navigate_to"):
                discovery.add(session_id)
            if e.action == "propose" and e.outcome == "pending":
                proposal.add(session_id)
                if e.intent == "request_checkout":
                    checkout_proposed.add(session_id)
            if e.action == "confirm" and e.outcome == "success":
                confirmed.add(session_id)
                if (e.details or {}).get("action_type") in _MUTATION_ACTION_TYPES:
                    cart_mutated.add(session_id)

    ordered = _count_sessions_with_outcome(db, tenant_id, set(by_session), "ordered")

    return FunnelMetrics(
        sessions=len(by_session),
        discovery=len(discovery),
        proposal=len(proposal),
        confirmed=len(confirmed),
        cart_mutated=len(cart_mutated),
        checkout_proposed=len(checkout_proposed),
        ordered=ordered,
    )


def _events_in_range(
    db: Session, tenant_id: uuid.UUID, start: datetime, end: datetime
) -> list[AssistantEvent]:
    stmt = sa.select(AssistantEvent).where(
        AssistantEvent.tenant_id == tenant_id,
        AssistantEvent.occurred_at >= start,
        AssistantEvent.occurred_at < end,
    )
    return list(db.scalars(stmt).all())


def _count_sessions_with_outcome(
    db: Session, tenant_id: uuid.UUID, session_ids: set[str], outcome: str
) -> int:
    if not session_ids:
        return 0
    stmt = (
        sa.select(sa.func.count())
        .select_from(ConversationSessionRecord)
        .where(
            ConversationSessionRecord.tenant_id == tenant_id,
            ConversationSessionRecord.session_id.in_(session_ids),
            ConversationSessionRecord.outcome == outcome,
        )
    )
    return db.scalar(stmt) or 0


def _percentile(values: list[int], p: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * p
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return float(ordered[f])
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)
