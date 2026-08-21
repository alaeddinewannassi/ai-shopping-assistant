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
from src.agent.llm_client import RuleBasedStubClient, create_llm_client
from src.agent.pending import PendingActionGate
from src.logging.audit import log_action
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Placeholder turn handler.

    Full dialogue logic (search/navigate/propose/confirm flows across US1-US4) lives in
    agent/dialogue.py once implemented (T022+). For now this demonstrates the wiring: intent
    parsing -> a read-only adapter call for search intents, with graceful
    AdapterUnavailableError handling per FR-016/research.md §8.
    """
    action = _llm_client.parse_turn(request.message, context={})

    if action.action_type == "search_products":
        try:
            products = _adapter.search_products(query=action.parameters.get("query", ""))
            log_action(request.session_id, action.action_type, "search_products", "success")
            if not products:
                reply = "I couldn't find anything matching that — want to try a different search?"
            else:
                names = ", ".join(p.name for p in products[:5])
                reply = f"Here's what I found: {names}"
        except AdapterUnavailableError:
            log_action(request.session_id, action.action_type, "search_products", "unavailable")
            reply = (
                "I can't reach the store's catalog right now, so I can't search reliably. "
                "Please try again in a moment."
            )
    else:
        reply = (
            f"(Recognized intent: {action.action_type} — full handling for this intent is "
            f"implemented as part of its user story, see tasks.md.)"
        )

    return ChatResponse(session_id=request.session_id, reply=reply)
