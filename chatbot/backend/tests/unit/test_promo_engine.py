"""Unit tests for the promo rule engine `evaluate()` (T050, contracts/promo-strategy.md).

Pure fixture rules, no adapter/store involved — the engine is a policy layer only.
"""

from __future__ import annotations

from src.adapters.base import Cart, CartLine
from src.promo.engine import evaluate
from src.promo.strategy import PromoStrategyRule


def _cart(subtotal: float) -> Cart:
    # CartLine.line_total drives Cart.subtotal, so build a single line matching the target.
    return Cart(id="cart-1", lines=[CartLine(product_id="p1", variant_id="v1", quantity=1, unit_price=subtotal)])


def test_single_matching_rule_is_suggested() -> None:
    rules = [
        PromoStrategyRule(rule_id="big-cart", condition="subtotal >= 100", target_code="BIGCART15", priority=10),
    ]
    suggestions = evaluate(_cart(150.0), {"first_order": False}, rules)
    assert [s.code for s in suggestions] == ["BIGCART15"]


def test_no_matching_rule_returns_empty_list() -> None:
    rules = [
        PromoStrategyRule(rule_id="big-cart", condition="subtotal >= 100", target_code="BIGCART15", priority=10),
    ]
    suggestions = evaluate(_cart(20.0), {"first_order": False}, rules)
    assert suggestions == []


def test_multiple_matching_stackable_rules_are_all_returned() -> None:
    rules = [
        PromoStrategyRule(
            rule_id="welcome", condition="first_order", target_code="WELCOME10",
            priority=5, stackable_with=["big-cart"],
        ),
        PromoStrategyRule(
            rule_id="big-cart", condition="subtotal >= 100", target_code="BIGCART15",
            priority=10, stackable_with=["welcome"],
        ),
    ]
    suggestions = evaluate(_cart(150.0), {"first_order": True}, rules)
    assert [s.rule_id for s in suggestions] == ["big-cart", "welcome"]  # priority order


def test_multiple_matching_exclusive_rules_resolve_by_priority() -> None:
    rules = [
        PromoStrategyRule(rule_id="welcome", condition="first_order", target_code="WELCOME10", priority=5),
        PromoStrategyRule(rule_id="big-cart", condition="subtotal >= 100", target_code="BIGCART15", priority=10),
    ]
    suggestions = evaluate(_cart(150.0), {"first_order": True}, rules)
    assert [s.code for s in suggestions] == ["BIGCART15"]  # only the higher-priority one


def test_load_rules_reads_default_demo_strategy() -> None:
    from src.promo.strategy import load_rules

    rules = load_rules()
    codes = {r.target_code for r in rules}
    assert codes == {"WELCOME10", "BIGCART15"}
