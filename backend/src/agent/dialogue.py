"""Dialogue orchestration for User Stories 1 and 2 (T024, T025, T027, T033-T038).

`handle_turn()` is the single entrypoint api/chat.py calls for a conversational turn: it
parses the intent via the configured LLMClient, then routes to the discovery handler (US1)
or the cart propose/confirm/decline flow (US2, gated by PendingActionGate — the sole code
path allowed to mutate the cart, research.md §9.3).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.adapters.base import AdapterUnavailableError, CommerceAdapter, OutOfStockError
from src.agent.intents import (
    CartIntentHandler,
    CartResolution,
    CartResolutionKind,
    DiscoveryIntentHandler,
    DiscoveryKind,
    DiscoveryOutcome,
)
from src.agent.llm_client import LLMClient
from src.agent.pending import PendingActionError, PendingActionGate
from src.agent.recap import (
    build_add_to_cart_recap,
    build_cart_summary,
    build_remove_cart_recap,
    build_update_cart_recap,
)
from src.agent.taxonomy_resolver import Candidate
from src.logging.audit import log_action
from src.session.store import ConversationSession, SessionStore


@dataclass
class DialogueContext:
    """Bundles every dependency a conversational turn might need, so `handle_turn` has a
    single, stable parameter regardless of how many user stories' handlers are wired in."""

    session_store: SessionStore
    llm_client: LLMClient
    discovery_handler: DiscoveryIntentHandler
    adapter: CommerceAdapter
    cart_handler: CartIntentHandler | None = None
    pending_gate: PendingActionGate | None = None


def _format_products(products, *, limit: int = 5) -> str:
    return "; ".join(f"{p.name} (${p.base_price:.2f})" for p in products[:limit])


def _format_clarifying_question(candidates: list[Candidate] | list[str]) -> str:
    labels = [c.display_label if isinstance(c, Candidate) else c for c in candidates[:5]]
    options = ", ".join(labels)
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


def _cart_id_for(session: ConversationSession) -> str:
    return session.cart_id or session.session_id


def _handle_propose_add_to_cart(ctx: DialogueContext, session_id: str, raw_text: str) -> str:
    assert ctx.cart_handler is not None and ctx.pending_gate is not None
    resolution = ctx.cart_handler.resolve_add_to_cart(raw_text)

    if resolution.kind == CartResolutionKind.UNAVAILABLE:
        log_action(session_id, "propose_add_to_cart", "search_products", "unavailable")
        return (
            "I can't reach the store's catalog right now, so I can't verify that product. "
            "Please try again in a moment."
        )
    if resolution.kind == CartResolutionKind.NOT_FOUND:
        return "I couldn't find a product matching that — could you tell me its name?"
    if resolution.kind == CartResolutionKind.AMBIGUOUS_PRODUCT:
        return _format_clarifying_question(resolution.candidates)
    if resolution.kind == CartResolutionKind.AMBIGUOUS_VARIANT:
        assert resolution.product is not None
        return (
            f"Which option of {resolution.product.name} did you mean — "
            + _format_clarifying_question(resolution.candidates)
        )
    if resolution.kind == CartResolutionKind.OUT_OF_STOCK:
        assert resolution.product is not None and resolution.variant is not None
        if resolution.alternatives:
            alt = ", ".join(
                ", ".join(f"{k}: {v}" for k, v in alt_variant.attributes.items())
                for alt_variant in resolution.alternatives
            )
            return (
                f"Sorry, {resolution.product.name} ({', '.join(f'{k}: {v}' for k, v in resolution.variant.attributes.items())}) "
                f"is out of stock. In-stock options: {alt}."
            )
        return f"Sorry, {resolution.product.name} is out of stock right now, with no in-stock alternative."

    assert resolution.kind == CartResolutionKind.RESOLVED
    assert resolution.product is not None and resolution.variant is not None
    recap = build_add_to_cart_recap(resolution.product, resolution.variant, resolution.quantity)
    action = ctx.pending_gate.propose(
        session_id,
        "add_cart_item",
        {
            "product_id": resolution.product.id,
            "variant_id": resolution.variant.id,
            "quantity": resolution.quantity,
        },
        recap,
    )
    log_action(session_id, "propose_add_to_cart", "propose", "pending", details={"action_id": action.action_id})
    return f"{recap} (reply 'yes' to confirm or 'no' to cancel)"


def _handle_propose_cart_line_change(
    ctx: DialogueContext, session_id: str, raw_text: str, *, remove: bool
) -> str:
    assert ctx.cart_handler is not None and ctx.pending_gate is not None
    session = ctx.session_store.get_or_create(session_id)
    try:
        cart = ctx.adapter.get_cart(_cart_id_for(session))
    except AdapterUnavailableError:
        return (
            "I can't reach your cart right now, so I can't verify that change. "
            "Please try again in a moment."
        )

    resolution = ctx.cart_handler.resolve_cart_line_reference(cart, raw_text)
    if resolution.kind == CartResolutionKind.UNAVAILABLE:
        return "I can't reach the store's catalog right now to verify that item. Please try again in a moment."
    if resolution.kind == CartResolutionKind.LINE_NOT_FOUND:
        return "I couldn't find that item in your cart — could you tell me its name?"
    if resolution.kind == CartResolutionKind.AMBIGUOUS_PRODUCT:
        return _format_clarifying_question(resolution.candidates)

    assert resolution.kind == CartResolutionKind.RESOLVED and resolution.line is not None
    line = resolution.line
    try:
        product = ctx.adapter.get_product(line.product_id)
    except AdapterUnavailableError:
        return "I can't reach the store's catalog right now to verify that item. Please try again in a moment."

    if remove:
        recap = build_remove_cart_recap(product, line)
        action = ctx.pending_gate.propose(
            session_id, "remove_cart_item", {"variant_id": line.variant_id}, recap
        )
    else:
        new_quantity = resolution.quantity
        recap = build_update_cart_recap(product, line, new_quantity)
        action = ctx.pending_gate.propose(
            session_id,
            "update_cart_item",
            {"variant_id": line.variant_id, "quantity": new_quantity},
            recap,
        )
    log_action(
        session_id,
        "propose_remove_from_cart" if remove else "propose_update_cart",
        "propose",
        "pending",
        details={"action_id": action.action_id},
    )
    return f"{recap} (reply 'yes' to confirm or 'no' to cancel)"


def _handle_confirm(ctx: DialogueContext, session_id: str) -> str:
    assert ctx.pending_gate is not None
    session = ctx.session_store.get_or_create(session_id)
    pending = session.pending_action
    if pending is None:
        log_action(session_id, "confirm_pending_action", "confirm", "nothing_pending")
        return "There's nothing pending for me to confirm right now."

    try:
        result = ctx.pending_gate.confirm(session_id, pending.action_id)
    except PendingActionError:
        log_action(session_id, "confirm_pending_action", "confirm", "stale_or_missing")
        return "That confirmation isn't valid anymore — could you tell me again what you'd like to do?"
    except AdapterUnavailableError:
        # T035a: never assume success, never fall back to a cache for a mutation.
        log_action(session_id, "confirm_pending_action", "confirm", "unavailable")
        return (
            "I couldn't apply that change — the store is temporarily unreachable. "
            "Nothing was changed; please try again shortly."
        )
    except OutOfStockError:
        log_action(session_id, "confirm_pending_action", "confirm", "out_of_stock")
        return "Sorry, that item just went out of stock, so I couldn't complete that change."

    log_action(session_id, "confirm_pending_action", "confirm", "success")
    if result.cart is None:
        return "Done!"

    products_by_id = {}
    for line in result.cart.lines:
        try:
            products_by_id[line.product_id] = ctx.adapter.get_product(line.product_id)
        except Exception:  # noqa: BLE001 - best-effort display name lookup only
            continue
    return build_cart_summary(result.cart, products_by_id)


def _handle_decline(ctx: DialogueContext, session_id: str) -> str:
    assert ctx.pending_gate is not None
    ctx.pending_gate.decline(session_id)
    log_action(session_id, "decline_pending_action", "decline", "declined")
    return "No problem, I won't make that change. What would you like to do instead?"


def handle_turn(ctx: DialogueContext, session_id: str, message: str) -> str:
    """Handles one conversational turn across US1 (discovery/navigation) and US2 (cart
    propose/confirm/decline). Any other recognized action_type is acknowledged but not yet
    actionable (implemented as its user story lands)."""
    session = ctx.session_store.get_or_create(session_id)
    action = ctx.llm_client.parse_turn(
        message, context={"navigation_context": session.navigation_context}
    )

    if action.action_type == "search_products":
        outcome = ctx.discovery_handler.handle_search(action.parameters.get("query", message))
        _record_navigation(ctx.session_store, session, outcome)
        log_action(session_id, action.action_type, "search_products", outcome.kind.value)
        return render_discovery_reply(outcome)

    if action.action_type == "navigate_to":
        outcome = ctx.discovery_handler.handle_navigate(action.parameters.get("target", message))
        _record_navigation(ctx.session_store, session, outcome)
        log_action(session_id, action.action_type, "navigate_to", outcome.kind.value)
        return render_discovery_reply(outcome)

    if action.action_type == "propose_add_to_cart" and ctx.cart_handler and ctx.pending_gate:
        return _handle_propose_add_to_cart(
            ctx, session_id, action.parameters.get("raw_text", message)
        )

    if action.action_type == "propose_update_cart" and ctx.cart_handler and ctx.pending_gate:
        return _handle_propose_cart_line_change(
            ctx, session_id, action.parameters.get("raw_text", message), remove=False
        )

    if action.action_type == "propose_remove_from_cart" and ctx.cart_handler and ctx.pending_gate:
        return _handle_propose_cart_line_change(
            ctx, session_id, action.parameters.get("raw_text", message), remove=True
        )

    if action.action_type == "confirm_pending_action" and ctx.pending_gate:
        return _handle_confirm(ctx, session_id)

    if action.action_type == "decline_pending_action" and ctx.pending_gate:
        return _handle_decline(ctx, session_id)

    return (
        f"(Recognized intent: {action.action_type} — full handling for this intent is "
        f"implemented as part of its user story; see tasks.md.)"
    )

