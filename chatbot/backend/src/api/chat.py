"""FastAPI app: POST /chat, GET /health, GET /audit/{session_id} (T016, T065, T203).

Each request resolves its tenant via `resolve_tenant_runtime` (src/tenancy/resolver.py,
T202) — the widget's `X-Assistant-Key` header, or `DEFAULT_TENANT_SLUG`'s legacy env-driven
config when no key is sent or the tenancy database isn't configured (plan.md D2). Every
tenant gets its own adapter/LLM client/session store, pooled with a short TTL
(src/tenancy/runtime.py, T203) — this module no longer builds any of that at import time.
"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from tenancy_db import engine as db_engine

from src.adapters.base import AdapterUnavailableError, CommerceAdapter
from src.agent.dialogue import handle_turn
from src.logging.audit import dropped_event_count, get_audit_history
from src.tenancy import TenantRuntime, resolve_tenant_runtime

app = FastAPI(title="AI Shopping Assistant", version="0.1.0")

# The widget (widget/src) is designed to be embedded on the merchant's storefront, an
# origin that's necessarily different from wherever this API is hosted — allow any origin
# rather than hardcoding the demo store's URL, matching a public-facing chat widget's
# actual deployment shape. Per-tenant origin allowlisting (widget_key.allowed_origins) is
# enforced in src/tenancy/resolver.py when a request carries X-Assistant-Key; this
# middleware-level wildcard is what makes an unkeyed (legacy single-tenant) request work at
# all — tightening it further is T206.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str


def _check_redis() -> str:
    """T065: reports live Redis reachability, distinct from "not configured" — SessionStore
    silently falls back to an in-memory store in both cases, so /health is the only place
    this distinction is surfaced (research.md §8 gap noted in T066's audit review)."""
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return "not_configured"
    try:
        import redis as redis_lib

        client = redis_lib.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        return "ok"
    except Exception:  # noqa: BLE001 - readiness check must never raise, only report
        return "unavailable"


def _check_adapter(adapter: CommerceAdapter) -> str:
    """T065: a cheap read-only call proves the configured CommerceAdapter can actually
    reach its store right now, not just that it was constructed successfully at startup."""
    try:
        adapter.list_categories()
        return "ok"
    except AdapterUnavailableError:
        return "unavailable"
    except Exception:  # noqa: BLE001 - readiness check must never raise, only report
        return "unavailable"


@app.get("/health")
def health(runtime: TenantRuntime = Depends(resolve_tenant_runtime)) -> dict[str, object]:
    """Reports readiness for the resolved tenant (default tenant if no X-Assistant-Key is
    sent, preserving pre-002 single-tenant behavior exactly). `database` is informational
    only — the tenancy/analytics database is optional infra (plan.md D1); its absence must
    never make a deployment that can still serve chat turns report itself as degraded.

    `dropped_events` (T703) is the analytics event-writer's overflow counter
    (logging/audit.py) — process-lifetime, not per-tenant, and not persisted anywhere the
    backoffice can query directly yet (that would need either a shared adapter-style
    package or a cross-service call, the same open question as adapter "Test connection",
    specs/002-backoffice-analytics/plan.md). Surfaced here so it's at least visible to
    whoever/whatever already polls this endpoint."""
    redis_status = _check_redis()
    adapter_status = _check_adapter(runtime.adapter)
    overall = "ok" if adapter_status == "ok" and redis_status != "unavailable" else "degraded"
    return {
        "status": overall,
        "adapter": adapter_status,
        "redis": redis_status,
        "database": db_engine.check_health(),
        "tenant": runtime.config.slug,
        "dropped_events": dropped_event_count(),
    }


@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest, runtime: TenantRuntime = Depends(resolve_tenant_runtime)
) -> ChatResponse:
    """Handles one conversational turn for the resolved tenant.

    Discovery/navigation (US1), cart propose/confirm/decline (US2), checkout (US3), and
    promo suggestion/apply (US4) intents are fully wired via agent/dialogue.py.
    """
    reply = handle_turn(runtime.dialogue_ctx, request.session_id, request.message)
    return ChatResponse(session_id=request.session_id, reply=reply)


@app.get("/audit/{session_id}")
def audit(
    session_id: str, runtime: TenantRuntime = Depends(resolve_tenant_runtime)
) -> dict[str, object]:
    """Returns this session's audit trail (FR-014) — every navigation change, cart
    mutation, promo suggestion/application, and checkout action logged so far, oldest
    first. Empty list for an unknown, expired, or brand-new session.

    Reads from the tenancy database (T310) for the resolved tenant when it's configured and
    reachable, falling back to the pre-002 Redis/in-memory path otherwise — same response
    shape either way, so this endpoint's contract is unchanged."""
    events = get_audit_history(session_id, tenant_id=runtime.config.tenant_id)
    return {"session_id": session_id, "events": events}
