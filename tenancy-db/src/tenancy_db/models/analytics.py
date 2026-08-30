"""Assistant event stream + per-session rollup (T301).

`AssistantEvent` is the enriched, durable replacement for chatbot/backend's stdout-only
audit log — one row per `log_action()` call, now carrying `tenant_id`/`turn_id`/`seq` so
events can be grouped per conversational turn and scoped per tenant. `ConversationSession`
is a small upserted summary row (one per session) used for session lists/funnels without
scanning the full event stream.

`llm_provider`/`llm_model`/`prompt_tokens`/`completion_tokens`/`llm_ms`/`cost_micros` are
populated only on the `turn_completed` event of a turn that made a real LLM call
(`FreeTierHostedLLMClient`, chatbot/backend/src/agent/llm_client.py) — every other event on
every turn, and every field on a `rule-based-stub` turn, stays NULL. Not yet partitioned by
month as originally scoped — a plain indexed table today, monthly partitioning is a
documented follow-up once real volume needs it (T301 follow-up, not a correctness
requirement).

Write path: only `chatbot/backend` writes here (every chat turn), via a bounded queue +
background thread (`chatbot/backend/src/logging/audit.py`) so a slow/unavailable database
never blocks a chat turn. `backoffice/backend` and `chatbot/backend`'s own
`GET /audit/{session_id}` only ever read.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from tenancy_db.base import Base, JSONType, created_at_column, uuid_pk


class AssistantEvent(Base):
    __tablename__ = "assistant_event"

    event_id: Mapped[object] = uuid_pk()
    tenant_id: Mapped[object] = mapped_column(sa.Uuid, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(sa.String(200), nullable=False, index=True)
    turn_id: Mapped[object] = mapped_column(sa.Uuid, nullable=False, index=True)
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    occurred_at = created_at_column()
    intent: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    action: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    outcome: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    turn_elapsed_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)

    # Real LLM usage (only on a turn_completed event whose turn made an actual model call —
    # see module docstring). cost_micros is 0 for the free-tier profile (Groq's free tier
    # genuinely costs nothing); the column exists for when hosted-paid is real.
    llm_provider: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    llm_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    cost_micros: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)

    __table_args__ = (
        sa.Index("ix_assistant_event_tenant_occurred", "tenant_id", "occurred_at"),
        sa.Index("ix_assistant_event_session_seq", "tenant_id", "session_id", "turn_id", "seq"),
    )


class ConversationSessionRecord(Base):
    """One row per (tenant, session) — upserted on every turn. Named *Record to avoid
    colliding with chatbot/backend's own in-memory `ConversationSession`
    (src/session/store.py), which is unrelated hot-path state, not analytics."""

    __tablename__ = "conversation_session"

    id: Mapped[object] = uuid_pk()
    tenant_id: Mapped[object] = mapped_column(sa.Uuid, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    started_at = created_at_column()
    last_seen_at: Mapped[object] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    turn_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    # browsing -> cart -> ordered. "abandoned" is a time-based classification (Phase 4's
    # scheduler job, not knowable from a single turn) and isn't set here.
    outcome: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="browsing")
    cart_id: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    order_id: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)

    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "session_id", name="uq_conversation_session_tenant_session"),
    )
