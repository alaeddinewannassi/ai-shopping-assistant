"""Unit tests for _resolve_reference_to_last_shown (agent/intents.py) — the deterministic
pronoun/ordinal resolver that lets "add it"/"the second one" resolve against what a shopper
was just shown, without needing another LLM round trip or a fresh (blind) keyword search."""

from __future__ import annotations

from src.agent.intents import _resolve_reference_to_last_shown


def test_bare_pronoun_resolves_when_exactly_one_product_was_shown() -> None:
    assert _resolve_reference_to_last_shown("add it to my cart", ["prod-1"]) == "prod-1"


def test_bare_pronoun_stays_ambiguous_when_several_products_were_shown() -> None:
    # "it" alone genuinely doesn't say which one — guessing would be worse than asking.
    assert _resolve_reference_to_last_shown("add it to my cart", ["prod-1", "prod-2"]) is None


def test_ordinal_reference_picks_the_right_index() -> None:
    ids = ["prod-1", "prod-2", "prod-3"]
    assert _resolve_reference_to_last_shown("add the second one", ids) == "prod-2"
    assert _resolve_reference_to_last_shown("I'll take the first one", ids) == "prod-1"
    assert _resolve_reference_to_last_shown("give me the last one", ids) == "prod-3"


def test_ordinal_reference_out_of_range_returns_none() -> None:
    assert _resolve_reference_to_last_shown("the third one", ["prod-1"]) is None


def test_named_product_is_not_treated_as_a_reference() -> None:
    # A specific, real name should fall through to the ordinary keyword search instead of
    # being (mis)treated as a pronoun/ordinal reference.
    assert _resolve_reference_to_last_shown("add the blue jacket", ["prod-1"]) is None


def test_nothing_to_resolve_against_when_no_products_were_shown() -> None:
    assert _resolve_reference_to_last_shown("add it", []) is None
