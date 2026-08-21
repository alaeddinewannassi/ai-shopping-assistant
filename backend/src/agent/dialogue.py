"""Discovery/navigation dialogue orchestration (T024, T025, T027).

`handle_turn()` is the single entrypoint api/chat.py calls for User Story 1 turns: it parses
the intent via the configured LLMClient, delegates to DiscoveryIntentHandler, updates
`ConversationSession.navigation_context` on a successful category navigation (T024), renders
a natural-language reply, and audit-logs every navigation change (FR-014).
"""

from __future__ import annotations

from src.agent.intents import DiscoveryIntentHandler, DiscoveryKind, DiscoveryOutcome
from src.agent.llm_client import LLMClient
from src.agent.taxonomy_resolver import Candidate
from src.logging.audit import log_action
from src.session.store import ConversationSession, SessionStore


def _format_products(products, *, limit: int = 5) -> str:
    return "; ".join(f"{p.name} (${p.base_price:.2f})" for p in products[:limit])


def _format_clarifying_question(candidates: list[Candidate]) -> str:
    options = ", ".join(c.display_label for c in candidates[:5])
    return f"I found a few matching options — did you mean: {options}?"


def render_discovery_reply(outcome: DiscoveryOutcome) -> str:
    if outcome.kind == DiscoveryKind.PRODUCTS:
        prefix = ""
        if outcome.degraded:
            prefix = (
                "(Showing recently cached results — the store's live catalog is temporarily "
                "unreachable, so this may be outdated.) "
            )
        return prefix + f"Here's what I found: {_format_products(outcome.products)}"

    if outcome.kind == DiscoveryKind.NAVIGATE_CATEGORY:
        assert outcome.category is not None
        if not outcome.products:
            return (
                f"You're now browsing {outcome.category.display_label}, but there are no "
                f"products in it right now."
            )
        return f"Here's the {outcome.category.display_label} category: {_format_products(outcome.products)}"

    if outcome.kind == DiscoveryKind.CLARIFY:
        return _format_clarifying_question(outcome.clarifying_options)

    if outcome.kind == DiscoveryKind.NO_MATCH:
        return (
            "I couldn't find anything matching that. Want to try a broader search, or "
            "browse a category instead?"
        )

    if outcome.kind == DiscoveryKind.UNAVAILABLE:
        return (
            "I can't reach the store's catalog right now, so I can't search reliably. "
            "Please try again in a moment."
        )

    return "I'm not sure how to help with that yet."  # pragma: no cover - exhaustive enum


def _record_navigation(
    session_store: SessionStore, session: ConversationSession, outcome: DiscoveryOutcome
) -> None:
    if outcome.kind == DiscoveryKind.NAVIGATE_CATEGORY and outcome.category is not None:
        session.navigation_context = {
            "type": "category",
            "category_id": outcome.category.id,
            "label": outcome.category.display_label,
        }
        session_store.save(session)


def handle_turn(
    session_store: SessionStore,
    llm_client: LLMClient,
    discovery_handler: DiscoveryIntentHandler,
    session_id: str,
    message: str,
) -> str:
    """Handles one conversational turn for the discovery/navigation intents (US1).

    Any other recognized action_type (cart/promo/checkout intents) is left to later user
    stories' dialogue wiring; this function returns a placeholder acknowledgement for those
    so `POST /chat` is fully functional end-to-end even before US2-US4 land.
    """
    session = session_store.get_or_create(session_id)
    action = llm_client.parse_turn(
        message, context={"navigation_context": session.navigation_context}
    )

    if action.action_type == "search_products":
        outcome = discovery_handler.handle_search(action.parameters.get("query", message))
        _record_navigation(session_store, session, outcome)
        log_action(session_id, action.action_type, "search_products", outcome.kind.value)
        return render_discovery_reply(outcome)

    if action.action_type == "navigate_to":
        outcome = discovery_handler.handle_navigate(action.parameters.get("target", message))
        _record_navigation(session_store, session, outcome)
        log_action(session_id, action.action_type, "navigate_to", outcome.kind.value)
        return render_discovery_reply(outcome)

    return (
        f"(Recognized intent: {action.action_type} — full handling for this intent is "
        f"implemented as part of its user story; see tasks.md.)"
    )
