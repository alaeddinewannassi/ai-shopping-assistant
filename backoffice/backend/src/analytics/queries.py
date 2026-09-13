"""Dashboard query layer (T403/T404) — reads only, over the raw assistant_event /
conversation_session tables chatbot/backend writes (T301-T310).

Scoped down from the original plan: no rollup-table routing yet (T401's analytics_hourly/
analytics_daily tables and T402's scheduler aren't built — see
specs/002-backoffice-analytics/plan.md's Phase 4 status). Every function here scans raw
events directly, which is always correct and simple, just not yet optimized for large date
ranges — the honest, testable building block D5's rollup-vs-raw routing would eventually
sit in front of. `get_timeseries` below was added the same way (raw scan, correct now,
same optimization opportunity later) rather than waiting on that deferred infrastructure —
see its own docstring.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session
from tenancy_db.models.analytics import AssistantEvent, ConversationSessionRecord

# Outcome strings that mean something genuinely broke (not the shopper's fault) —
# out_of_stock/declined/cart_state_changed/promo_invalid/empty_cart are legitimate business
# outcomes, never errors. Real, confirmed live gap: this used to only ever count
# "unavailable" (the store/adapter genuinely unreachable), missing "error" entirely — the
# outcome llm_client.py's parse_turn logs on a real LLM API failure (e.g. a 400 from the
# provider). That specific failure showed up in a real session's raw event log, gracefully
# recovered from at the shopper-facing level (a safe fallback reply, no crash) — but Overview's
# error_rate stayed at 0%, hiding a real infrastructure problem an admin should see.
# "rate_limited" (llm_client.py's _RateLimitExceededError) is its own distinct outcome, not
# folded into "error" — it isn't a bug, it's Groq's free-tier allowance being temporarily
# exhausted — but it still means a shopper's turn didn't get real LLM help, which an admin
# genuinely needs visibility into (it's actionable: reduce traffic, switch model, or upgrade).
_ERROR_OUTCOMES = {"unavailable", "error", "rate_limited"}

_MUTATION_ACTION_TYPES = {"add_cart_item", "update_cart_item", "remove_cart_item", "apply_promo"}


@dataclass
class OverviewMetrics:
    session_count: int
    turn_count: int
    # NOT ordered_session_count/conversion_rate — the same real, confirmed live gap as the
    # Funnel's old "Ordered" bar (see FunnelMetrics.checkout_handed_off's docstring): for
    # every client-cart-synced tenant (every real PrestaShop store this project targets),
    # checkout always hands off to PrestaShop's own native checkout, so a session outcome of
    # "ordered" can never actually fire — a "conversion rate" stat built on it would always
    # read 0.0%, which a merchant would reasonably (and wrongly) read as "nobody buys here."
    # checkout_rate tracks the thing this pipeline can actually observe: real purchase
    # intent, the fraction of sessions that reached checkout. Fully closing the loop (a
    # truly confirmed order) needs a separate integration — not done here.
    checkout_handed_off_count: int
    checkout_rate: float  # checkout_handed_off_count / session_count, 0.0 if no sessions
    avg_turn_latency_ms: float | None
    p95_turn_latency_ms: float | None
    error_event_count: int
    error_rate: float  # error_event_count / non-turn_completed events, 0.0 if none
    # Groq's live rate-limit headroom for the model in use, as of the most recent real LLM
    # call in the selected range (turn_context.py's record_llm_usage rode these onto that
    # turn_completed event's details — see log_turn_completed). All None when no turn in
    # range made a real call (e.g. RuleBasedStubClient, or simply no traffic yet this range) —
    # this is a point-in-time snapshot, not an aggregate, so there's nothing to show instead
    # of "unknown" in that case. llm_snapshot_at says how fresh the reading is; there's no
    # live-poll alternative that wouldn't itself burn the very quota it's reporting on.
    llm_requests_limit: int | None = None
    llm_requests_remaining: int | None = None
    llm_tokens_limit: int | None = None
    llm_tokens_remaining: int | None = None
    llm_snapshot_at: str | None = None


@dataclass
class FunnelMetrics:
    sessions: int
    discovery: int
    proposal: int
    confirmed: int
    cart_mutated: int
    checkout_proposed: int
    # NOT "ordered" — a real, confirmed live gap found reviewing the dashboard: for every
    # client-cart-synced tenant (every real PrestaShop store this project targets), checkout
    # always hands off to PrestaShop's OWN native checkout page instead of completing the
    # order through this backend, so an "ordered" session outcome can never actually fire —
    # that bar would sit at 0 forever regardless of how many shoppers really buy, which is
    # actively misleading in a funnel (looks like nobody ever converts). checkout_handed_off
    # tracks the thing this pipeline CAN honestly observe: real purchase intent, the moment a
    # session was handed off to complete a purchase — see dialogue.py's
    # _upsert_conversation_session for the "checkout" outcome this counts.
    checkout_handed_off: int


@dataclass
class DailyPoint:
    date: str  # ISO calendar date (YYYY-MM-DD), tenant-agnostic UTC day boundary
    session_count: int
    turn_count: int
    # Real LLM tokens actually consumed that day (prompt_tokens + completion_tokens, summed
    # from turn_completed events that made a real model call — see turn_context.py's
    # record_llm_usage). Already-captured data, no new instrumentation needed: this just
    # reads columns that were already being written for the p95-latency fix, to answer "am I
    # about to run out of my free-tier quota" with a real trend instead of a guess.
    llm_tokens: int = 0


def get_overview(db: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> OverviewMetrics:
    """Overview panel: activity, checkout rate, latency, error rate for `[start, end)`."""
    events = _events_in_range(db, tenant_id, start, end)
    session_ids = {e.session_id for e in events}
    turn_events = [e for e in events if e.intent == "turn_completed"]
    latencies = [e.turn_elapsed_ms for e in turn_events if e.turn_elapsed_ms is not None]
    non_turn_events = [e for e in events if e.intent != "turn_completed"]
    error_count = sum(1 for e in non_turn_events if e.outcome in _ERROR_OUTCOMES)

    checkout_count = _count_sessions_with_outcome(db, tenant_id, session_ids, {"checkout", "ordered"})
    llm_snapshot = _latest_llm_ratelimit_snapshot(turn_events)

    return OverviewMetrics(
        session_count=len(session_ids),
        turn_count=len(turn_events),
        checkout_handed_off_count=checkout_count,
        checkout_rate=(checkout_count / len(session_ids)) if session_ids else 0.0,
        avg_turn_latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
        p95_turn_latency_ms=_percentile(latencies, 0.95) if latencies else None,
        error_event_count=error_count,
        error_rate=(error_count / len(non_turn_events)) if non_turn_events else 0.0,
        **llm_snapshot,
    )


def _latest_llm_ratelimit_snapshot(turn_events: list[AssistantEvent]) -> dict:
    candidates = [e for e in turn_events if (e.details or {}).get("ratelimit_remaining_requests") is not None]
    if not candidates:
        return {}
    latest = max(candidates, key=lambda e: e.occurred_at)
    details = latest.details
    return {
        "llm_requests_limit": details.get("ratelimit_limit_requests"),
        "llm_requests_remaining": details.get("ratelimit_remaining_requests"),
        "llm_tokens_limit": details.get("ratelimit_limit_tokens"),
        "llm_tokens_remaining": details.get("ratelimit_remaining_tokens"),
        "llm_snapshot_at": latest.occurred_at.isoformat(),
    }


def get_funnel(db: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> FunnelMetrics:
    """Funnel panel: sessions -> discovery -> proposal -> confirmed -> cart_mutated ->
    checkout_proposed -> checkout_handed_off, each a DISTINCT session count (a session can
    land in multiple stages — that's the point of a funnel, not a bug). No "ordered" stage —
    see FunnelMetrics.checkout_handed_off's docstring for why that would be misleading here."""
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

    checkout_handed_off = _count_sessions_with_outcome(db, tenant_id, set(by_session), {"checkout", "ordered"})

    return FunnelMetrics(
        sessions=len(by_session),
        discovery=len(discovery),
        proposal=len(proposal),
        confirmed=len(confirmed),
        cart_mutated=len(cart_mutated),
        checkout_proposed=len(checkout_proposed),
        checkout_handed_off=checkout_handed_off,
    )


def get_timeseries(db: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> list[DailyPoint]:
    """Sessions/turns per calendar day for `[start, end]` — added so the Overview page can
    plot a trend instead of one flat aggregate for the whole selected range, which was the
    single biggest gap found reviewing the dashboard live: an admin had no way to tell
    whether activity was growing, shrinking, or when within the period something changed.

    One point per UTC calendar day, inclusive of both endpoints' dates, with explicit
    zero-fill for days with no activity — a chart needs an evenly-spaced x-axis, not just
    the days that happened to have events. `session_count` counts a session on every day it
    had ANY event (matching get_overview's own "distinct session_id in range" definition);
    `turn_count` counts turn_completed events specifically, also matching get_overview.

    Same raw-scan approach as get_overview/get_funnel above (correct now, not yet optimized
    for a large date range — see this module's docstring), not the deferred rollup-table
    routing this project's contract originally reserved timeseries for. That infrastructure
    doesn't exist yet, and at this project's current event volume a raw scan costs nothing
    a shopper or admin would notice; swapping the query underneath is a later, separate
    optimization, not a reason to withhold the chart today."""
    events = _events_in_range(db, tenant_id, start, end)
    sessions_by_day: dict[str, set[str]] = {}
    turns_by_day: dict[str, int] = {}
    tokens_by_day: dict[str, int] = {}
    for e in events:
        day = e.occurred_at.date().isoformat()
        sessions_by_day.setdefault(day, set()).add(e.session_id)
        if e.intent == "turn_completed":
            turns_by_day[day] = turns_by_day.get(day, 0) + 1
            tokens_by_day[day] = (
                tokens_by_day.get(day, 0) + (e.prompt_tokens or 0) + (e.completion_tokens or 0)
            )

    points: list[DailyPoint] = []
    current = start.date()
    last_day = end.date()
    while current <= last_day:
        key = current.isoformat()
        points.append(
            DailyPoint(
                date=key,
                session_count=len(sessions_by_day.get(key, ())),
                turn_count=turns_by_day.get(key, 0),
                llm_tokens=tokens_by_day.get(key, 0),
            )
        )
        current += timedelta(days=1)
    return points


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
    db: Session, tenant_id: uuid.UUID, session_ids: set[str], outcomes: str | set[str]
) -> int:
    """`outcomes` is usually a single string, but "reached checkout" must also count a
    session that went all the way to "ordered" — outcome only ever ranks upward
    (upsert_turn), so "ordered" implies checkout was reached too, even though the stored
    value itself no longer says "checkout" once it's moved past it. Pass a set to count
    "reached at least one of these" rather than an exact match."""
    if not session_ids:
        return 0
    wanted = {outcomes} if isinstance(outcomes, str) else outcomes
    stmt = (
        sa.select(sa.func.count())
        .select_from(ConversationSessionRecord)
        .where(
            ConversationSessionRecord.tenant_id == tenant_id,
            ConversationSessionRecord.session_id.in_(session_ids),
            ConversationSessionRecord.outcome.in_(wanted),
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
