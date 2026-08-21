"""FastAPI app skeleton (T016): POST /chat, GET /health.

Wires the session store + adapter + pending-action gate together. Full dialogue
orchestration (intent parsing, taxonomy resolution, recap building) is implemented in
agent/dialogue.py as part of each user story's tasks (T022+); this skeleton provides the
HTTP surface and a minimal placeholder handler so the service is runnable end-to-end early.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel

from src.adapters.base import AdapterUnavailableError, CommerceAdapter
from src.adapters.mock import MockAdapter
from src.agent.dialogue import DialogueContext, handle_turn
from src.agent.intents import CartIntentHandler, DiscoveryIntentHandler, PromoIntentHandler
from src.agent.llm_client import RuleBasedStubClient, create_llm_client
from src.agent.pending import PendingActionGate
from src.agent.taxonomy_resolver import TaxonomyResolver
from src.promo.strategy import load_rules
from src.session.catalog_cache import CatalogSnapshotCache
from src.session.store import SessionStore

app = FastAPI(title="AI Shopping Assistant", version="0.1.0")


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str


def _build_adapter() -> CommerceAdapter:
    """Returns the configured CommerceAdapter: PrestaShopAdapter (T012) once
    PRESTASHOP_BASE_URL/PRESTASHOP_API_KEY are set (backend/.env.example), else MockAdapter
    for local/dev/test runs with no store configured."""
    if os.environ.get("PRESTASHOP_BASE_URL") and os.environ.get("PRESTASHOP_API_KEY"):
        from src.adapters.prestashop import PrestaShopAdapter

        return PrestaShopAdapter()
    return MockAdapter()


def _build_llm_client():
    provider = os.environ.get("LLM_PROVIDER", "rule-based-stub")
    try:
        return create_llm_client(provider=provider)
    except ValueError:
        # Fall back to the deterministic stub if the configured provider can't be built
        # (e.g. missing LLM_API_KEY) so the service is still runnable for local dev/demo.
        return RuleBasedStubClient()


_adapter = _build_adapter()
_session_store = SessionStore()
_pending_gate = PendingActionGate(_session_store, _adapter)
_llm_client = _build_llm_client()
_taxonomy_resolver = TaxonomyResolver(_adapter)
_catalog_cache = CatalogSnapshotCache()
_discovery_handler = DiscoveryIntentHandler(_adapter, _taxonomy_resolver, _catalog_cache)
_cart_handler = CartIntentHandler(_adapter)
_promo_handler = PromoIntentHandler(_adapter)
_promo_rules = load_rules()
_dialogue_ctx = DialogueContext(
    session_store=_session_store,
    llm_client=_llm_client,
    discovery_handler=_discovery_handler,
    adapter=_adapter,
    cart_handler=_cart_handler,
    pending_gate=_pending_gate,
    promo_handler=_promo_handler,
    promo_rules=_promo_rules,
)


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


def _check_adapter() -> str:
    """T065: a cheap read-only call proves the configured CommerceAdapter can actually
    reach its store right now, not just that it was constructed successfully at startup."""
    try:
        _adapter.list_categories()
        return "ok"
    except AdapterUnavailableError:
        return "unavailable"
    except Exception:  # noqa: BLE001 - readiness check must never raise, only report
        return "unavailable"


@app.get("/health")
def health() -> dict[str, str]:
    redis_status = _check_redis()
    adapter_status = _check_adapter()
    overall = "ok" if adapter_status == "ok" and redis_status != "unavailable" else "degraded"
    return {"status": overall, "adapter": adapter_status, "redis": redis_status}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Handles one conversational turn.

    Discovery/navigation (US1), cart propose/confirm/decline (US2), checkout (US3), and
    promo suggestion/apply (US4) intents are fully wired via agent/dialogue.py.
    """
    reply = handle_turn(_dialogue_ctx, request.session_id, request.message)
    return ChatResponse(session_id=request.session_id, reply=reply)
