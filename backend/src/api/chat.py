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

from src.adapters.base import CommerceAdapter
from src.adapters.mock import MockAdapter
from src.agent.dialogue import DialogueContext, handle_turn
from src.agent.intents import CartIntentHandler, DiscoveryIntentHandler
from src.agent.llm_client import RuleBasedStubClient, create_llm_client
from src.agent.pending import PendingActionGate
from src.agent.taxonomy_resolver import TaxonomyResolver
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
    """Returns the configured CommerceAdapter.

    Defaults to MockAdapter for local/dev/test runs; swap to PrestaShopAdapter once T012
    is implemented and PRESTASHOP_BASE_URL/PRESTASHOP_API_KEY are set (backend/.env.example).
    """
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
_dialogue_ctx = DialogueContext(
    session_store=_session_store,
    llm_client=_llm_client,
    discovery_handler=_discovery_handler,
    adapter=_adapter,
    cart_handler=_cart_handler,
    pending_gate=_pending_gate,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Handles one conversational turn.

    Discovery/navigation (US1) and cart propose/confirm/decline (US2) intents are fully
    wired via agent/dialogue.py. Checkout/promo intents (US3-US4) will be wired the same
    way as their user stories land; for now they're acknowledged but not yet actionable.
    """
    reply = handle_turn(_dialogue_ctx, request.session_id, request.message)
    return ChatResponse(session_id=request.session_id, reply=reply)
