"""Dialogue orchestration for User Stories 1 and 2 (T024, T025, T027, T033-T038).

`handle_turn()` is the single entrypoint api/chat.py calls for a conversational turn: it
parses the intent via the configured LLMClient, then routes to the discovery handler (US1)
or the cart propose/confirm/decline flow (US2, gated by PendingActionGate — the sole code
path allowed to mutate the cart, research.md §9.3).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from src.adapters.base import (
    AdapterUnavailableError,
    CartStateChangedError,
    CommerceAdapter,
    OutOfStockError,
    ProductNotFoundError,
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
from src.agent.llm_client import ActionCall, LLMClient
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

    if outcome.kind == DiscoveryKind.PRODUCT_DETAILS:
        assert outcome.products
        product = outcome.products[0]
        if not product.variants:
            return f"{product.name} is ${product.base_price:.2f} — it doesn't have size/color options."
        options = []
        for variant in product.variants:
            attrs = ", ".join(f"{k}: {v}" for k, v in variant.attributes.items())
            status = "in stock" if variant.in_stock else "out of stock"
            options.append(f"{attrs} ({status})")
        return f"{product.name} (${product.base_price:.2f}) comes in: {'; '.join(options)}."

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
        # "does it fit a young man?" has something real to connect "it" to, and as the
        # deterministic reference list a bare "add it"/"the second one" resolves against
        # (CartIntentHandler.resolve_add_to_cart) — same 5-item cap as _format_products.
        session.last_shown_products = _format_products(outcome.products)
        session.last_shown_product_ids = [p.id for p in outcome.products[:5]]
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


# A bare number ("2", "3 please") replying to an add_cart_item proposal still awaiting
# yes/no is unambiguous by construction — there is exactly one pending proposal and exactly
# one number, no product/variant guessing needed at all. A real LLM, given no better tool for
# "adjust the quantity of what you just proposed," classified this as propose_update_cart
# instead — a poor fit, since nothing is in the cart yet to update ("I couldn't find that
# item in your cart"). Resolved deterministically here instead, matching this codebase's
# existing posture that mutation-adjacent decisions should be structural where a structural
# answer exists (Constitution Principle III), not left to a model's guess — and the LLM isn't
# even called for a turn this fires on.
_BARE_QUANTITY_REPLY = re.compile(r"^\s*(\d+)\s*(?:please|pcs?|items?)?\s*[.!]?\s*$", re.IGNORECASE)


def _pending_add_quantity_override(session: ConversationSession, message: str) -> int | None:
    """Returns the shopper's requested new quantity IFF this turn is a bare-number reply to
    a still-open add_cart_item proposal, else None (falls through to normal LLM routing)."""
    pending = session.pending_action
    if pending is None or pending.action_type != "add_cart_item":
        return None
    match = _BARE_QUANTITY_REPLY.match(message)
    if match is None:
        return None
    return max(1, int(match.group(1)))


def _handle_pending_add_quantity_override(ctx: DialogueContext, session_id: str, quantity: int) -> str:
    """Replaces the currently-pending add_cart_item proposal with the SAME product/variant at
    a new quantity. Reuses the pending action's own stored product_id/variant_id directly —
    never re-resolved from the shopper's bare "2" — so this can never accidentally re-propose
    a DIFFERENT item than the one actually being adjusted."""
    assert ctx.pending_gate is not None
    session = ctx.session_store.get_or_create(session_id)
    pending = session.pending_action
    assert pending is not None and pending.action_type == "add_cart_item"
    product_id = pending.parameters["product_id"]
    variant_id = pending.parameters["variant_id"]

    try:
        product = ctx.adapter.get_product(product_id)
    except AdapterUnavailableError as exc:
        log_action(session_id, "propose_add_to_cart", "get_product", "unavailable", details={"error": str(exc)[:500]})
        return (
            "I can't reach the store's catalog right now, so I can't verify that product. "
            "Please try again in a moment."
        )
    except ProductNotFoundError:
        return "I couldn't find a product matching that — could you tell me its name?"

    variant = next((v for v in product.variants if v.id == variant_id), None)
    if variant is None:
        return "I couldn't find a product matching that — could you tell me its name?"

    recap = build_add_to_cart_recap(product, variant, quantity)
    action = ctx.pending_gate.propose(
        session_id,
        "add_cart_item",
        {"product_id": product.id, "variant_id": variant.id, "quantity": quantity},
        recap,
    )
    log_action(
        session_id, "propose_add_to_cart", "propose", "pending",
        details={"action_id": action.action_id, "quantity_override": quantity},
    )
    return f"{recap} (reply 'yes' to confirm or 'no' to cancel)"


def _handle_propose_add_to_cart(
    ctx: DialogueContext, session_id: str, raw_text: str, last_shown_ids: list[str]
) -> str:
    assert ctx.cart_handler is not None and ctx.pending_gate is not None
    session = ctx.session_store.get_or_create(session_id)
    resolution = ctx.cart_handler.resolve_add_to_cart(raw_text, last_shown_ids)

    def _clear_pending_variant() -> None:
        if session.pending_variant_product_id is not None:
            session.pending_variant_product_id = None
            session.pending_variant_product_name = ""
            ctx.session_store.save(session)

    if resolution.kind == CartResolutionKind.UNAVAILABLE:
        # Deliberately leaves any pending_variant_product_id untouched — this is a transient
        # adapter outage, not an answer to (or abandonment of) the open variant question, so a
        # retry should still resolve against the same product.
        log_action(session_id, "propose_add_to_cart", "search_products", "unavailable")
        return (
            "I can't reach the store's catalog right now, so I can't verify that product. "
            "Please try again in a moment."
        )
    if resolution.kind == CartResolutionKind.NOT_FOUND:
        _clear_pending_variant()
        return "I couldn't find a product matching that — could you tell me its name?"
    if resolution.kind == CartResolutionKind.AMBIGUOUS_PRODUCT:
        _clear_pending_variant()
        return _format_clarifying_question(resolution.candidates)
    if resolution.kind == CartResolutionKind.AMBIGUOUS_VARIANT:
        assert resolution.product is not None
        # Persists across turns (unlike the last_turn_* session fields) so a bare follow-up
        # answer like "size S white" — which matches no product name on its own — has
        # something to resolve against on the NEXT turn instead of falling back to whatever
        # was last searched/shown, which may be a stale, unrelated result. See
        # ConversationSession.pending_variant_product_id's docstring for the full rationale.
        session.pending_variant_product_id = resolution.product.id
        session.pending_variant_product_name = resolution.product.name
        ctx.session_store.save(session)
        return (
            f"Which option of {resolution.product.name} did you mean — "
            + _format_clarifying_question(resolution.candidates)
        )
    if resolution.kind == CartResolutionKind.OUT_OF_STOCK:
        assert resolution.product is not None and resolution.variant is not None
        _clear_pending_variant()
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
    _clear_pending_variant()
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


def _handle_confirm(ctx: DialogueContext, session_id: str) -> tuple[str, str | None]:
    """Returns (reply, confirmed_action_type) — confirmed_action_type is the PendingAction's
    own action_type ("add_cart_item"/"checkout"/...) ONLY on genuine success, None on every
    failure/no-op path (including CartStateChangedError's re-prompt). _route_turn uses this
    to decide whether to auto-navigate to the real cart page — never on anything short of
    an actual, confirmed mutation."""
    assert ctx.pending_gate is not None
    session = ctx.session_store.get_or_create(session_id)
    pending = session.pending_action
    if pending is None:
        log_action(session_id, "confirm_pending_action", "confirm", "nothing_pending")
        return "There's nothing pending for me to confirm right now.", None

    try:
        result = ctx.pending_gate.confirm(session_id, pending.action_id)
    except PendingActionError:
        log_action(session_id, "confirm_pending_action", "confirm", "stale_or_missing")
        return "That confirmation isn't valid anymore — could you tell me again what you'd like to do?", None
    except AdapterUnavailableError as exc:
        # T035a: never assume success, never fall back to a cache for a mutation.
        log_action(session_id, "confirm_pending_action", "confirm", "unavailable", details={"error": str(exc)[:500]})
        return (
            "I couldn't apply that change — the store is temporarily unreachable. "
            "Nothing was changed; please try again shortly."
        ), None
    except OutOfStockError:
        log_action(session_id, "confirm_pending_action", "confirm", "out_of_stock")
        return "Sorry, that item just went out of stock, so I couldn't complete that change.", None
    except CartStateChangedError:
        # FR-009 / US3 Scenario 4: re-validate and require a fresh confirmation instead of
        # retrying blindly or silently placing a mismatched order.
        return _handle_checkout_state_changed(ctx, session_id), None

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
        # Re-fetch fresh rather than reusing the STALE `session` read at the top of this
        # function — ctx.pending_gate.confirm() above already independently cleared
        # pending_action (its own get_or_create()+save() round trip, in its `finally`). Saving
        # the stale object here would silently resurrect that already-spent PendingAction,
        # confirmed=False again — a stray later "yes" could then re-trigger and re-execute the
        # SAME checkout/mutation (research.md §9.4's "a stray later 'yes' must never confirm a
        # stale, no-longer-relevant proposal" — this was a real gap in that guarantee).
        session = ctx.session_store.get_or_create(session_id)
        session.has_completed_order = True
        ctx.session_store.save(session)
        return (
            f"Order placed! Your order id is {order.id}. "
            f"Total charged: ${order.grand_total:.2f}. Thank you for shopping with us!"
        ), pending.action_type

    if result.cart is None:
        return "Done!", pending.action_type
    return build_cart_summary(result.cart, _products_by_id_for_cart(ctx, result.cart)), pending.action_type


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

# Action types whose reply may be handed to phrase_reply for natural rephrasing — read-only
# discovery only. See _route_turn's comment for why cart/checkout/confirm/decline/promo never
# are: a real test proved the model can fabricate a false mutation-completion claim.
_PHRASABLE_ACTION_TYPES = {"search_products", "navigate_to", "get_product_details"}

# The SECOND gate on phrase_reply, orthogonal to the action-type check above: even within a
# read-only discovery action, only a genuinely resolved/positive outcome may be phrased.
# CLARIFY (multiple candidates, nothing resolved), NO_MATCH, and UNAVAILABLE always stay
# exact — a live test proved rephrasing a CLARIFY outcome fabricates a false resolution
# claim, the discovery-side twin of the cart-mutation hallucination above.
_PHRASABLE_DISCOVERY_KINDS = {DiscoveryKind.PRODUCTS, DiscoveryKind.NAVIGATE_CATEGORY, DiscoveryKind.PRODUCT_DETAILS}

# Action types worth offering a real link to PrestaShop's own cart page for — every turn
# that's cart/checkout-adjacent, regardless of outcome (even a clarifying question is worth
# letting the shopper cross-check against the real cart, per docker/README-two-stores.md's
# documented gap: PrestaShop's own cart UI doesn't visually reflect chatbot mutations).
_CART_LINK_ACTION_TYPES = {
    "propose_add_to_cart",
    "propose_update_cart",
    "propose_remove_from_cart",
    "confirm_pending_action",
    "decline_pending_action",
    "request_checkout",
    "apply_promo",
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


def handle_turn(
    ctx: DialogueContext, session_id: str, message: str, *, customer_email: str | None = None
) -> str:
    """Handles one conversational turn across US1 (discovery/navigation), US2 (cart
    propose/confirm/decline), US3 (checkout), and US4 (promo suggestions/apply). Any other
    recognized action_type is acknowledged but not yet actionable.

    `customer_email` (api/chat.py's ChatRequest.customer_email, widget-read from
    window.prestashop.customer.email) mirrors onto this session every turn — set when a real
    shopper is logged in, cleared (None) for anonymous/guest or after they log out — and is
    handed to the adapter so cart/checkout attributes to that real account instead of the
    tenant's shared demo identity (PrestaShopAdapter.set_customer_context)."""
    with turn_scope(ctx.tenant_id, session_id):
        session = ctx.session_store.get_or_create(session_id)
        if session.real_customer_email != customer_email:
            session.real_customer_email = customer_email
            ctx.session_store.save(session)
        ctx.adapter.set_customer_context(session_id, customer_email)

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
    if session.pending_variant_product_id:
        # Nudges the LLM to route a bare attribute answer ("size S white") to
        # propose_add_to_cart instead of misreading it as a fresh search_products query or
        # ask_or_chat — dialogue.py's own reference-resolution (_resolve_single_product's
        # last-shown fallback) is what actually resolves it correctly once routed there; this
        # is only about getting the action_type classification right.
        context["pending_variant_product"] = session.pending_variant_product_name
    return context


def _route_turn(ctx: DialogueContext, session_id: str, message: str) -> str:
    session = ctx.session_store.get_or_create(session_id)

    pending_quantity_override = _pending_add_quantity_override(session, message)
    if pending_quantity_override is not None and ctx.cart_handler and ctx.pending_gate:
        # Deterministic fast path — see _pending_add_quantity_override's docstring. The LLM
        # is never even asked to classify this turn: there's nothing for it to judge.
        action = ActionCall(action_type="propose_add_to_cart", parameters={"raw_text": message})
    else:
        action = ctx.llm_client.parse_turn(
            message, context=_build_llm_context(session), session_id=session_id
        )

    # Populated by the branches below, then written onto the session at the very end so
    # api/chat.py can turn them into real product/cart links after handle_turn returns —
    # the same post-turn-session-read pattern as needs_confirmation, not a return-type
    # change to handle_turn/_route_turn (which dozens of existing tests treat as plain str).
    product_links: list[tuple[str, str]] = []
    shows_cart_link = action.action_type in _CART_LINK_ACTION_TYPES
    # Set only when exactly one product is unambiguously the focus of this turn (never for a
    # multi-result list — which one would we even pick?) or a cart mutation was genuinely
    # confirmed (never on the initial propose, which still needs a yes/no answer). The widget
    # only actually navigates if window.prestashop.page says the shopper isn't already there.
    auto_navigate_product_id: str | None = None
    auto_navigate_to_cart = False
    # Which of the discovery outcome kinds this turn actually produced, if any — a SECOND
    # gate on phrase_reply below, tighter than just "the action type was read-only". A real
    # test caught this exact gap: get_product_details' CLARIFY outcome (multiple candidates,
    # nothing resolved) still got phrased into a confident "here's the sweater you asked
    # about" — a false resolution claim, the same class of problem as the cart-mutation
    # hallucination, just one step earlier. Only a genuinely resolved/positive outcome may
    # be phrased; CLARIFY/NO_MATCH/UNAVAILABLE always stay exact templates.
    discovery_outcome_kind: DiscoveryKind | None = None

    if action.action_type == "search_products":
        query = action.parameters.get("query", message)
        outcome = ctx.discovery_handler.handle_search(query)
        _record_navigation(ctx.session_store, session, outcome)
        log_action(session_id, action.action_type, "search_products", outcome.kind.value, details={"query": query})
        reply = render_discovery_reply(outcome)
        discovery_outcome_kind = outcome.kind
        if outcome.kind == DiscoveryKind.PRODUCTS:
            # Mutually exclusive with auto-navigate, not additive: a link to a page we're
            # about to redirect to (or already on) is redundant — only show links for the
            # genuinely ambiguous multi-result case.
            if len(outcome.products) == 1:
                auto_navigate_product_id = outcome.products[0].id
            else:
                product_links = [(p.id, p.name) for p in outcome.products[:5]]

    elif action.action_type == "navigate_to":
        target = action.parameters.get("target", message)
        outcome = ctx.discovery_handler.handle_navigate(target)
        _record_navigation(ctx.session_store, session, outcome)
        log_action(session_id, action.action_type, "navigate_to", outcome.kind.value, details={"target": target})
        reply = render_discovery_reply(outcome)
        discovery_outcome_kind = outcome.kind
        if outcome.kind == DiscoveryKind.NAVIGATE_CATEGORY:
            if len(outcome.products) == 1:
                auto_navigate_product_id = outcome.products[0].id
            else:
                product_links = [(p.id, p.name) for p in outcome.products[:5]]

    elif action.action_type == "get_product_details":
        # Answers "what sizes/colors do you have?" with real catalog data — resolved via the
        # same reference logic as propose_add_to_cart, but strictly read-only (never proposes
        # anything). Added specifically because the LLM was otherwise misusing search_products
        # for this (rewriting the query using context instead of the shopper's own words).
        raw_text = action.parameters.get("raw_text", message)
        outcome = ctx.discovery_handler.resolve_product_details(raw_text, session.last_shown_product_ids)
        _record_navigation(ctx.session_store, session, outcome)
        log_action(session_id, action.action_type, "get_product_details", outcome.kind.value)
        reply = render_discovery_reply(outcome)
        discovery_outcome_kind = outcome.kind
        if outcome.kind == DiscoveryKind.PRODUCT_DETAILS:
            # Always exactly one product by construction — always auto-navigate, never a
            # redundant link to the page we're about to redirect to (or already on).
            auto_navigate_product_id = outcome.products[0].id

    elif action.action_type == "propose_add_to_cart" and ctx.cart_handler and ctx.pending_gate:
        if pending_quantity_override is not None:
            reply = _handle_pending_add_quantity_override(ctx, session_id, pending_quantity_override)
        else:
            # An open "which size/color?" question takes priority over last_shown_product_ids
            # — a bare follow-up like "size S white" answers THAT question, not a fresh
            # reference to whatever was most recently searched/shown (may be stale/unrelated).
            reference_ids = (
                [session.pending_variant_product_id]
                if session.pending_variant_product_id
                else session.last_shown_product_ids
            )
            reply = _handle_propose_add_to_cart(
                ctx, session_id, action.parameters.get("raw_text", message), reference_ids
            )

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
        reply, confirmed_type = _handle_confirm(ctx, session_id)
        if confirmed_type in {"add_cart_item", "update_cart_item", "remove_cart_item"}:
            auto_navigate_to_cart = True

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
        final_reply = reply
    else:
        final_reply = _maybe_suggest_promo(ctx, session_id, reply)

    # Re-read the freshest state right before this final save — several branches above
    # (_handle_propose_add_to_cart, PendingActionGate.propose/confirm/decline,
    # _handle_request_checkout, _handle_apply_promo, _maybe_suggest_promo, ...) do their own
    # independent get_or_create()+save() round trips on this SAME session_id. Against Redis,
    # every get_or_create() deserializes a BRAND NEW object from whatever's currently stored —
    # unlike the in-memory test double, which hands back one shared object reference and so
    # never surfaces this. Saving the STALE `session` object loaded at the top of this
    # function (before any of that happened) would silently overwrite everything those nested
    # calls just committed — a real, confirmed live bug: a just-created pending_action was
    # wiped out immediately after creation, so the shopper's very next "yes" always found
    # "nothing pending" to confirm. Re-fetching here picks their commits up before layering
    # this turn's own last_turn_* fields on top of them.
    session = ctx.session_store.get_or_create(session_id)
    # Reset every turn (never accumulates stale links from an earlier, unrelated turn) —
    # api/chat.py reads these right after handle_turn returns to build real, clickable
    # product/cart links (window.location.origin + these ids, computed client-side — see
    # widget.ts — so no tenant-specific public storefront URL needs configuring here).
    session.last_turn_product_ids = [pid for pid, _ in product_links]
    session.last_turn_product_names = [name for _, name in product_links]
    # Mutually exclusive with auto-navigate-to-cart — a link to the page we're about to
    # redirect to (or already on) is redundant.
    session.last_turn_shows_cart_link = shows_cart_link and not auto_navigate_to_cart
    session.last_turn_auto_navigate_product_id = auto_navigate_product_id
    session.last_turn_auto_navigate_to_cart = auto_navigate_to_cart
    ctx.session_store.save(session)

    if action.action_type == "ask_or_chat":
        # Already the LLM's own words — rephrasing an LLM's own output would just spend a
        # second call for no benefit.
        return final_reply

    # Natural LLM phrasing is scoped to read-only discovery replies ONLY, and only a
    # genuinely resolved/positive one at that. Two live tests each caught a real, concrete
    # hallucination going further:
    #  1. Rephrasing what was actually an unresolved AMBIGUOUS_PRODUCT clarifying question
    #     (nothing proposed, session.pending_action was None) fabricated "Got it — I've
    #     added the Hummingbird printed sweater in size M to your cart." — a false claim of
    #     a mutation that never happened.
    #  2. Rephrasing get_product_details' own CLARIFY outcome (multiple candidates, nothing
    #     resolved) fabricated "Got it — here's the Hummingbird printed sweater you asked
    #     about." — a false resolution claim, one step earlier but the same failure mode.
    # Both are direct violations of confirm-before-mutate / never-fabricate-catalog-facts
    # (Constitution Principle III): a shopper could believe something happened, or was
    # found, when it wasn't. So cart/checkout/promo/confirm/decline replies, any reply that
    # now carries a fresh pending confirmation (e.g. a proactive promo suggestion tacked onto
    # an otherwise read-only reply), and any discovery outcome that ISN'T a genuinely
    # resolved PRODUCTS/NAVIGATE_CATEGORY/PRODUCT_DETAILS result (CLARIFY/NO_MATCH/
    # UNAVAILABLE always stay exact) — all always stay exact templates, never phrase_reply.
    if (
        action.action_type not in _PHRASABLE_ACTION_TYPES
        or discovery_outcome_kind not in _PHRASABLE_DISCOVERY_KINDS
        or session.pending_action is not None
    ):
        return final_reply

    # `final_reply` here is exact, deterministic, already-correct ground truth (search
    # results / product details) — phrase_reply may only rephrase it naturally, never
    # add/remove/change a fact (llm_client.py's _PHRASE_SYSTEM_PROMPT). RuleBasedStubClient
    # passes it through unchanged, so every existing exact-string test keeps working untouched.
    return ctx.llm_client.phrase_reply(final_reply, message, session_id=session_id)

