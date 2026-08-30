"""Unit tests for adapters/matching.py — the shared keyword-matching helper used by both
CommerceAdapter implementations' search and agent/intents.py's AND-narrowing."""

from __future__ import annotations

from src.adapters.matching import token_matches_name, token_matches_product


def test_plural_query_matches_singular_name() -> None:
    assert token_matches_name("posters", "Framed poster") is True


def test_hyphenless_query_matches_hyphenated_name() -> None:
    assert token_matches_name("tshirt", "Hummingbird printed t-shirt") is True


def test_hyphenated_query_matches_hyphenated_name() -> None:
    # Regression: an earlier version of this fix normalized only the product-name side,
    # breaking a query typed WITH the hyphen ("t-shirt") even though it's the more natural
    # spelling — caught by the existing adapter contract test suite.
    assert token_matches_name("t-shirt", "Classic T-Shirt") is True


def test_negation_word_does_not_false_match_an_unrelated_product() -> None:
    """Regression for a real bug: "I want the tshirt not the sweater" pulled in three
    unrelated notebook products, because "not" is a literal substring of "notebook" under
    the old loose substring-containment matching."""
    assert token_matches_name("not", "Hummingbird notebook") is False


def test_exact_word_still_matches() -> None:
    assert token_matches_name("sweater", "Hummingbird printed sweater") is True


def test_short_token_never_matches() -> None:
    assert token_matches_name("ok", "Hummingbird printed t-shirt") is False


def test_unrelated_word_does_not_match() -> None:
    assert token_matches_name("jacket", "Hummingbird printed t-shirt") is False


def test_token_matches_product_falls_back_to_description() -> None:
    assert token_matches_product(
        "cotton", "Hummingbird printed t-shirt", "Made of extra long staple pima cotton."
    ) is True


def test_token_matches_product_still_matches_name_with_no_description() -> None:
    assert token_matches_product("sweater", "Hummingbird printed sweater", "") is True


def test_token_matches_product_unrelated_word_matches_neither() -> None:
    assert token_matches_product(
        "waterproof", "Hummingbird printed t-shirt", "Made of extra long staple pima cotton."
    ) is False
