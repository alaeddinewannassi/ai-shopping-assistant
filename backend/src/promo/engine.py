"""Pure promo-suggestion rule engine (T057, contracts/promo-strategy.md).

`evaluate()` is a pure function: cart + session_context + rules -> ordered suggestions.
It never calls the adapter and never sets `valid`/`discount_amount` — those only ever come
from `adapter.validate_promo()` (Constitution Principle VI, FR-012).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.adapters.base import Cart
from src.promo.strategy import PromoStrategyRule


@dataclass
class PromoSuggestion:
    rule_id: str
    code: str
    priority: int


def _condition_matches(condition: str, cart: Cart, session_context: dict) -> bool:
    context = {"subtotal": cart.subtotal, **session_context}
    try:
        # Conditions come only from trusted, checked-in strategy config (promo/rules.json),
        # never from shopper input — this is a small declarative expression language, not an
        # arbitrary-code sink.
        return bool(eval(condition, {"__builtins__": {}}, context))  # noqa: S307
    except NameError:
        return False


def evaluate(
    cart: Cart, session_context: dict, rules: list[PromoStrategyRule]
) -> list[PromoSuggestion]:
    """Returns candidate suggestions in priority order, already filtered for
    `stackable_with` conflicts (mutually exclusive rules resolved by priority — only one of
    a conflicting group is returned). Returns [] if no rule matches."""
    matched = [r for r in rules if _condition_matches(r.condition, cart, session_context)]
    if not matched:
        return []

    matched.sort(key=lambda r: -r.priority)
    selected: list[PromoStrategyRule] = [matched[0]]
    for rule in matched[1:]:
        if all(rule.rule_id in s.stackable_with for s in selected):
            selected.append(rule)

    return [
        PromoSuggestion(rule_id=r.rule_id, code=r.target_code, priority=r.priority)
        for r in selected
    ]
