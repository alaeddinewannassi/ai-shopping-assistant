"""Unit tests for TaxonomyResolver (research.md §9, contracts/taxonomy-resolver.md).

Deterministic, no LLM/network involved — exercises exact/ambiguous/unsupported resolution.
"""

from __future__ import annotations

from src.adapters.mock import MockAdapter
from src.agent.taxonomy_resolver import ResolutionStatus, TaxonomyResolver


def make_resolver() -> TaxonomyResolver:
    return TaxonomyResolver(MockAdapter())


def test_resolve_category_exact_match() -> None:
    resolver = make_resolver()
    result = resolver.resolve_category("T-Shirts")
    assert result.status == ResolutionStatus.EXACT
    assert result.resolved_id == "cat-tshirts"


def test_resolve_category_synonym_match() -> None:
    """'tshirt'/'tee' must resolve to the real category via the curated synonym table."""
    resolver = make_resolver()
    for term in ["tshirt", "tee", "t shirt"]:
        result = resolver.resolve_category(term)
        assert result.status == ResolutionStatus.EXACT, f"failed for term={term!r}"
        assert result.resolved_id == "cat-tshirts"


def test_resolve_category_unsupported_term() -> None:
    resolver = make_resolver()
    result = resolver.resolve_category("nonexistent-category-xyz")
    assert result.status == ResolutionStatus.UNSUPPORTED
    assert result.candidates == []


def test_resolve_attribute_value_exact_match() -> None:
    resolver = make_resolver()
    result = resolver.resolve_attribute_value("Color", "Red")
    assert result.status == ResolutionStatus.EXACT
    assert result.resolved_id == "Red"


def test_resolve_attribute_value_unsupported_when_no_synonym_configured() -> None:
    """'maroon' has no configured synonym and doesn't literally match any real value —
    MUST be unsupported, never silently mapped to the closest color (research.md §9.1)."""
    resolver = make_resolver()
    result = resolver.resolve_attribute_value("Color", "maroon")
    assert result.status == ResolutionStatus.UNSUPPORTED


def test_resolve_attribute_value_unknown_group_is_unsupported() -> None:
    resolver = make_resolver()
    result = resolver.resolve_attribute_value("Material", "cotton")
    assert result.status == ResolutionStatus.UNSUPPORTED


def test_empty_or_whitespace_term_is_unsupported_not_a_universal_match() -> None:
    """Empty/whitespace terms must not substring-match every category/value."""
    resolver = make_resolver()
    for term in ["", "   "]:
        cat_result = resolver.resolve_category(term)
        assert cat_result.status == ResolutionStatus.UNSUPPORTED
        color_result = resolver.resolve_attribute_value("Color", term)
        assert color_result.status == ResolutionStatus.UNSUPPORTED


def test_never_invents_a_filter_id_not_present_in_snapshot() -> None:
    """Fuzz-style check (contracts/taxonomy-resolver.md): feeding nonsense terms must only
    ever produce UNSUPPORTED (or AMBIGUOUS/EXACT against real values), never a resolved_id
    that isn't a real category id or a real attribute value from the snapshot."""
    resolver = make_resolver()
    real_category_ids = {"cat-tshirts", "cat-jackets"}
    real_color_values = {"Red", "Blue", "Burgundy"}

    nonsense_terms = ["asdkjhasd", "🚀🚀🚀", "", "   ", "xyzcategorynotreal"]
    for term in nonsense_terms:
        cat_result = resolver.resolve_category(term)
        if cat_result.resolved_id is not None:
            assert cat_result.resolved_id in real_category_ids
        color_result = resolver.resolve_attribute_value("Color", term)
        if color_result.resolved_id is not None:
            assert color_result.resolved_id in real_color_values
