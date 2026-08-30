"""Dialogue orchestration for User Stories 1 and 2 (T024, T025, T027, T033-T038).

`handle_turn()` is the single entrypoint api/chat.py calls for a conversational turn: it
parses the intent via the configured LLMClient, then routes to the discovery handler (US1)
or the cart propose/confirm/decline flow (US2, gated by PendingActionGate — the sole code
path allowed to mutate the cart, research.md §9.3).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from src.adapters.base import (
    AdapterUnavailableError,
    CartStateChangedError,
    CommerceAdapter,
    OutOfStockError,
)
from src.agent.intents import (
    CartIntentHandler,
    CartResolutionKind,
    DiscoveryIntentHandler,
    DiscoveryKind,
    DiscoveryOutcome,
    PromoIntentHandler,
    PromoResolutionKind,
)
from src.agent.llm_client import LLMClient
from src.agent.pending import PendingActionError, PendingActionGate
from src.agent.recap import (
    build_add_to_cart_recap,
    build_cart_summary,
    build_checkout_recap,
    build_remove_cart_recap,
    build_update_cart_recap,
)
from src.agent.taxonomy_resolver import Candidate
from src.agent.turn_context import turn_scope
from src.logging.audit import log_action, log_turn_completed
from src.promo import engine as promo_engine
from src.promo.strategy import PromoStrategyRule
from src.session.store import ConversationSession, SessionStore

# Same logger name as src/agent/intents.py's — the shopper-facing "can't reach the store"
# replies below are deliberately generic (a real customer during a real outage shouldn't
# see internal config guidance); the adapter's actual exception message, which distinguishes
# "never configured" from "temporarily unreachable," goes here and into log_action's
# `details` instead, for whoever operates the store.
_logger = logging.getLogger("assistant.adapter")


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
    promo_handler: PromoIntentHandler | None = None
    promo_rules: list[PromoStrategyRule] | None = None
    # Set by src/tenancy/runtime.py's build_tenant_runtime() (T203). None for every
    # DialogueContext built directly by tests (pre-002 style) — handle_turn() below treats
    # that as "no tenant to attach to audit events," never as an error.
    tenant_id: uuid.UUID | None = None


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
    changed = False
    if outcome.kind == DiscoveryKind.NAVIGATE_CATEGORY and outcome.category is not None:
        session.navigation_context = {
            "type": "category",
            "category_id": outcome.category.id,
            "label": outcome.category.display_label,
        }
        changed = True
    if outcome.products:
        # Fed back as LLM context on the NEXT turn (_build_llm_context) so a follow-up like
        # "does it fit a young man?" has something real to connect "it" to.
        session.last_shown_products = _format_products(outcome.products)
        changed = True
    if changed:
        session_store.save(session)


def _cart_id_for(session: ConversationSession) -> str:
    return session.cart_id or session.session_id


def _products_by_id_for_cart(ctx: DialogueContext, cart) -> dict:
    products_by_id = {}
    for line in cart.lines:
        try:
            products_by_id[line.product_id] = ctx.adapter.get_product(line.product_id)
        except Exception:  # noqa: BLE001 - best-effort display name lookup only
            continue
    return products_by_id


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
    action_type = "propose_remove_from_cart" if remove else "propose_update_cart"
    try:
        cart = ctx.adapter.get_cart(_cart_id_for(session))
    except AdapterUnavailableError as exc:
        log_action(session_id, action_type, "get_cart", "unavailable", details={"error": str(exc)[:500]})
        return (
            "I can't reach your cart right now, so I can't verify that change. "
            "Please try again in a moment."
        )

    resolution = ctx.cart_handler.resolve_cart_line_reference(cart, raw_text)
    if resolution.kind == CartResolutionKind.UNAVAILABLE:
        log_action(session_id, action_type, "get_product", "unavailable")
        return "I can't reach the store's catalog right now to verify that item. Please try again in a moment."
    if resolution.kind == CartResolutionKind.LINE_NOT_FOUND:
        return "I couldn't find that item in your cart — could you tell me its name?"
    if resolution.kind == CartResolutionKind.AMBIGUOUS_PRODUCT:
        return _format_clarifying_question(resolution.candidates)

    assert resolution.kind == CartResolutionKind.RESOLVED and resolution.line is not None
    line = resolution.line
    try:
        product = ctx.adapter.get_product(line.product_id)
    except AdapterUnavailableError as exc:
        log_action(session_id, action_type, "get_product", "unavailable", details={"error": str(exc)[:500]})
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
        action_type,
        "propose",
        "pending",
        details={"action_id": action.action_id},
    )
    return f"{recap} (reply 'yes' to confirm or 'no' to cancel)"


def _handle_request_checkout(ctx: DialogueContext, session_id: str) -> str:
    """T045: builds the full checkout recap and proposes the `checkout` PendingAction, or
    short-circuits with no recap at all if the cart is empty (spec Edge Cases)."""
    assert ctx.pending_gate is not None
    session = ctx.session_store.get_or_create(session_id)
    try:
        cart = ctx.adapter.get_cart(_cart_id_for(session))
    except AdapterUnavailableError as exc:
        log_action(session_id, "request_checkout", "get_cart", "unavailable", details={"error": str(exc)[:500]})
        return (
            "I can't reach the store right now, so I can't start checkout. "
            "Please try again in a moment."
        )

    if not cart.lines:
        log_action(session_id, "request_checkout", "checkout", "empty_cart")
        return "Your cart is empty — want to keep browsing? I can help you find something."

    recap = build_checkout_recap(cart, _products_by_id_for_cart(ctx, cart))
    action = ctx.pending_gate.propose(session_id, "checkout", {}, recap)
    log_action(session_id, "request_checkout", "propose", "pending", details={"action_id": action.action_id})
    return f"{recap} Shall I place the order? (reply 'yes' to confirm or 'no' to cancel)"


def _handle_checkout_state_changed(ctx: DialogueContext, session_id: str) -> str:
    """FR-009 / US3 Scenario 4: cart/stock/price/promo changed between recap and
    confirmation. Re-validates against the store and requires a fresh confirmation instead
    of retrying blindly or silently placing a mismatched order."""
    assert ctx.pending_gate is not None
    session = ctx.session_store.get_or_create(session_id)
    try:
        cart = ctx.adapter.get_cart(_cart_id_for(session))
    except AdapterUnavailableError as exc:
        log_action(session_id, "confirm_pending_action", "checkout", "unavailable", details={"error": str(exc)[:500]})
        return "I can't reach the store right now to re-check your cart. Please try again shortly."

    if not cart.lines:
        log_action(session_id, "confirm_pending_action", "checkout", "cart_state_changed_empty")
        return (
            "Your cart changed (it's now empty) since I last showed you the recap, so I "
            "can't place that order. Want to keep browsing?"
        )

    recap = build_checkout_recap(cart, _products_by_id_for_cart(ctx, cart))
    action = ctx.pending_gate.propose(session_id, "checkout", {}, recap)
    log_action(
        session_id, "confirm_pending_action", "checkout", "cart_state_changed",
        details={"action_id": action.action_id},
    )
    return (
        "Something changed in your cart since I last showed you this (stock, price, or "
        f"promo) — here's the updated recap: {recap} Shall I place the order? "
        "(reply 'yes' to confirm or 'no' to cancel)"
    )


def _handle_apply_promo(ctx: DialogueContext, session_id: str, raw_text: str) -> str:
    """T059/T060: a shopper-provided code and a shopper accepting a proactive suggestion are
    handled identically — both go straight to `adapter.validate_promo()`
    (contracts/promo-strategy.md "Manually-provided codes"); the engine is only consulted
    for *proactive* suggestions (`_maybe_suggest_promo`), never here."""
    assert ctx.promo_handler is not None and ctx.pending_gate is not None
    session = ctx.session_store.get_or_create(session_id)
    cart_id = _cart_id_for(session)
    resolution = ctx.promo_handler.resolve_apply_promo(cart_id, raw_text)

    if resolution.kind == PromoResolutionKind.NO_CODE_GIVEN:
        return _describe_available_promos(ctx, session_id, session)
    if resolution.kind == PromoResolutionKind.UNAVAILABLE:
        log_action(session_id, "apply_promo", "validate_promo", "unavailable")
        return (
            "I can't reach the store right now, so I can't verify a promo code. "
            "Please try again in a moment."
        )
    if resolution.kind == PromoResolutionKind.INVALID:
        log_action(
            session_id, "apply_promo", "validate_promo", "invalid",
            details={"code": resolution.code, "reason": resolution.validation.reason},
        )
        return f"Sorry, {resolution.code} isn't valid for your cart right now ({resolution.validation.reason})."

    assert resolution.kind == PromoResolutionKind.RESOLVED
    validation = resolution.validation
    recap = f"Apply code {resolution.code} for a ${validation.discount_amount:.2f} discount?"
    action = ctx.pending_gate.propose(session_id, "apply_promo", {"code": resolution.code}, recap)
    log_action(
        session_id, "apply_promo", "propose", "pending",
        details={"action_id": action.action_id, "code": resolution.code},
    )
    return f"{recap} (reply 'yes' to confirm or 'no' to cancel)"


def _describe_available_promos(ctx: DialogueContext, session_id: str, session: ConversationSession) -> str:
    """US4 Scenario 5: honestly reports when no discount currently applies, rather than
    inventing one, when the shopper asks about promos without giving a specific code."""
    try:
        cart = ctx.adapter.get_cart(_cart_id_for(session))
    except AdapterUnavailableError as exc:
        log_action(session_id, "apply_promo", "get_cart", "unavailable", details={"error": str(exc)[:500]})
        return "I can't reach the store right now to check for promo codes. Please try again in a moment."

    if ctx.promo_rules and cart.lines and not cart.applied_promo_code:
        session_context = {"first_order": not session.has_completed_order}
        for suggestion in promo_engine.evaluate(cart, session_context, ctx.promo_rules):
            try:
                validation = ctx.adapter.validate_promo(_cart_id_for(session), suggestion.code)
            except AdapterUnavailableError as exc:
                _logger.warning("Adapter unavailable during promo check for %s: %s", session_id, exc)
                break
            if validation.valid:
                assert ctx.pending_gate is not None
                recap = (
                    f"You qualify for code {suggestion.code}, which would save you "
                    f"${validation.discount_amount:.2f}. Apply it to your cart?"
                )
                action = ctx.pending_gate.propose(session_id, "apply_promo", {"code": suggestion.code}, recap)
                log_action(
                    session_id, "promo_suggestion", "suggest", "shown",
                    details={"action_id": action.action_id, "code": suggestion.code},
                )
                return f"{recap} (reply 'yes' to confirm or 'no' to cancel)"

    log_action(session_id, "apply_promo", "check_available", "none")
    return "I don't see any discounts available for your cart right now."


def _maybe_suggest_promo(ctx: DialogueContext, session_id: str, reply: str) -> str:
    """T058: proactively suggests a store-validated promo code alongside a normal reply
    (US4 Scenario 1), never surfacing a candidate the store hasn't confirmed is valid
    (contracts/promo-strategy.md). Never overrides an in-flight PendingAction."""
    if ctx.promo_rules is None or ctx.pending_gate is None:
        return reply
    # Re-fetch: any propose() made while producing `reply` (e.g. an add-to-cart proposal)
    # must be reflected here so a promo suggestion never clobbers it.
    session = ctx.session_store.get_or_create(session_id)
    if session.pending_action is not None:
        return reply
    try:
        cart = ctx.adapter.get_cart(_cart_id_for(session))
    except AdapterUnavailableError as exc:
        _logger.warning("Adapter unavailable during proactive promo suggestion for %s: %s", session_id, exc)
        return reply
    if not cart.lines or cart.applied_promo_code:
        return reply

    session_context = {"first_order": not session.has_completed_order}
    for suggestion in promo_engine.evaluate(cart, session_context, ctx.promo_rules):
        try:
            validation = ctx.adapter.validate_promo(_cart_id_for(session), suggestion.code)
        except AdapterUnavailableError as exc:
            _logger.warning("Adapter unavailable during proactive promo suggestion for %s: %s", session_id, exc)
            return reply
        if not validation.valid:
            continue
        recap = (
            f"You qualify for code {suggestion.code}, which would save you "
            f"${validation.discount_amount:.2f}. Apply it to your cart?"
        )
        action = ctx.pending_gate.propose(session_id, "apply_promo", {"code": suggestion.code}, recap)
        log_action(
            session_id, "promo_suggestion", "suggest", "shown",
            details={"action_id": action.action_id, "code": suggestion.code},
        )
        return f"{reply}\n\n{recap} (reply 'yes' to confirm or 'no' to cancel)"
    return reply


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
    except AdapterUnavailableError as exc:
        # T035a: never assume success, never fall back to a cache for a mutation.
        log_action(session_id, "confirm_pending_action", "confirm", "unavailable", details={"error": str(exc)[:500]})
        return (
            "I couldn't apply that change — the store is temporarily unreachable. "
            "Nothing was changed; please try again shortly."
        )
    except OutOfStockError:
        log_action(session_id, "confirm_pending_action", "confirm", "out_of_stock")
        return "Sorry, that item just went out of stock, so I couldn't complete that change."
    except CartStateChangedError:
        # FR-009 / US3 Scenario 4: re-validate and require a fresh confirmation instead of
        # retrying blindly or silently placing a mismatched order.
        return _handle_checkout_state_changed(ctx, session_id)

    # details.action_type distinguishes a confirmed cart mutation from a confirmed checkout —
    # both share this same (intent, action, outcome) tuple otherwise, and the funnel query
    # (backoffice/backend/src/analytics/queries.py) needs that distinction (T404).
    log_action(
        session_id, "confirm_pending_action", "confirm", "success",
        details={"action_type": pending.action_type},
    )

    if pending.action_type == "checkout":
        assert result.order is not None
        order = result.order
        session.has_completed_order = True
        ctx.session_store.save(session)
        return (
            f"Order placed! Your order id is {order.id}. "
            f"Total charged: ${order.grand_total:.2f}. Thank you for shopping with us!"
        )

    if result.cart is None:
        return "Done!"
    return build_cart_summary(result.cart, _products_by_id_for_cart(ctx, result.cart))


def _handle_decline(ctx: DialogueContext, session_id: str) -> str:
    assert ctx.pending_gate is not None
    session = ctx.session_store.get_or_create(session_id)
    pending = session.pending_action
    ctx.pending_gate.decline(session_id)
    if pending is None:
        log_action(session_id, "decline_pending_action", "decline", "nothing_pending")
    else:
        # Record *what* was declined (not just that a decline happened) so the audit trail
        # can reconstruct which proposed action never went through (FR-014).
        log_action(
            session_id, "decline_pending_action", "decline", "declined",
            details={"action_id": pending.action_id, "declined_action_type": pending.action_type},
        )
    return "No problem, I won't make that change. What would you like to do instead?"


# Turns after which a fresh proactive promo suggestion would be noise (the promo flow
# itself, and final checkout/confirm/decline turns) — see _maybe_suggest_promo.
_SKIP_PROMO_SUGGESTION_AFTER = {
    "apply_promo",
    "confirm_pending_action",
    "decline_pending_action",
    "request_checkout",
}


def _upsert_conversation_session(ctx: DialogueContext, session_id: str) -> None:
    """Best-effort per-session analytics summary (T309) — a no-op with no tenant resolved
    (ctx.tenant_id is None, e.g. every DialogueContext built directly by pre-002 tests) or
    with the tenancy database unreachable. `outcome` only ever tracks "ordered" here: cart
    lines aren't visible without an extra adapter round-trip this function deliberately
    doesn't make (a chat turn must never pay analytics-classification latency, plan.md D4)
    — "cart" outcome classification is a documented gap, not silently faked."""
    if ctx.tenant_id is None:
        return
    from tenancy_db.engine import session_scope
    from tenancy_db.repositories import ConversationSessionRepository

    session = ctx.session_store.get_or_create(session_id)
    outcome = "ordered" if session.has_completed_order else None
    try:
        with session_scope() as db:
            if db is None:
                return
            ConversationSessionRepository(db).upsert_turn(
                ctx.tenant_id, session_id, cart_id=session.cart_id, outcome=outcome
            )
    except Exception:  # noqa: BLE001 - analytics upsert must never break a chat turn
        pass


def handle_turn(ctx: DialogueContext, session_id: str, message: str) -> str:
    """Handles one conversational turn across US1 (discovery/navigation), US2 (cart
    propose/confirm/decline), US3 (checkout), and US4 (promo suggestions/apply). Any other
    recognized action_type is acknowledged but not yet actionable."""
    with turn_scope(ctx.tenant_id, session_id):
        reply = _route_turn(ctx, session_id, message)
        log_turn_completed(session_id)
        _upsert_conversation_session(ctx, session_id)
        return reply


def _build_llm_context(session: ConversationSession) -> dict:
    """Context passed to LLMClient.parse_turn() — additive only, RuleBasedStubClient
    ignores it entirely. `pending_action` lets a real model correctly route "yes"/"actually,
    cancel that" against what's actually pending, rather than guessing from bare keywords."""
    context: dict = {"navigation_context": session.navigation_context}
    if session.last_shown_products:
        context["last_shown_products"] = session.last_shown_products
    if session.pending_action is not None:
        context["pending_action"] = {
            "action_type": session.pending_action.action_type,
            "recap_text": session.pending_action.recap_text,
        }
    return context


def _route_turn(ctx: DialogueContext, session_id: str, message: str) -> str:
    session = ctx.session_store.get_or_create(session_id)
    action = ctx.llm_client.parse_turn(
        message, context=_build_llm_context(session), session_id=session_id
    )

    if action.action_type == "search_products":
        outcome = ctx.discovery_handler.handle_search(action.parameters.get("query", message))
        _record_navigation(ctx.session_store, session, outcome)
        log_action(session_id, action.action_type, "search_products", outcome.kind.value)
        reply = render_discovery_reply(outcome)

    elif action.action_type == "navigate_to":
        outcome = ctx.discovery_handler.handle_navigate(action.parameters.get("target", message))
        _record_navigation(ctx.session_store, session, outcome)
        log_action(session_id, action.action_type, "navigate_to", outcome.kind.value)
        reply = render_discovery_reply(outcome)

    elif action.action_type == "propose_add_to_cart" and ctx.cart_handler and ctx.pending_gate:
        reply = _handle_propose_add_to_cart(ctx, session_id, action.parameters.get("raw_text", message))

    elif action.action_type == "propose_update_cart" and ctx.cart_handler and ctx.pending_gate:
        reply = _handle_propose_cart_line_change(
            ctx, session_id, action.parameters.get("raw_text", message), remove=False
        )

    elif action.action_type == "propose_remove_from_cart" and ctx.cart_handler and ctx.pending_gate:
        reply = _handle_propose_cart_line_change(
            ctx, session_id, action.parameters.get("raw_text", message), remove=True
        )

    elif action.action_type == "request_checkout" and ctx.pending_gate:
        reply = _handle_request_checkout(ctx, session_id)

    elif action.action_type == "apply_promo" and ctx.promo_handler and ctx.pending_gate:
        reply = _handle_apply_promo(ctx, session_id, action.parameters.get("raw_text", message))

    elif action.action_type == "confirm_pending_action" and ctx.pending_gate:
        reply = _handle_confirm(ctx, session_id)

    elif action.action_type == "decline_pending_action" and ctx.pending_gate:
        reply = _handle_decline(ctx, session_id)

    elif action.action_type == "ask_or_chat":
        # The one place the LLM's own words reach the shopper directly — everything else
        # in this file renders from deterministic outcomes. The system prompt (llm_client.py)
        # forbids stating catalog facts or answering off-topic questions here; this is purely
        # a conversational fallback for greetings/small talk/vague or exploratory messages.
        reply = action.parameters.get("text") or "How can I help you find something today?"
        log_action(session_id, action.action_type, "ask_or_chat", "ok")

    else:
        reply = (
            f"(Recognized intent: {action.action_type} — full handling for this intent is "
            f"implemented as part of its user story; see tasks.md.)"
        )

    if action.action_type in _SKIP_PROMO_SUGGESTION_AFTER:
        return reply
    return _maybe_suggest_promo(ctx, session_id, reply)

