"""Deterministic TaxonomyResolver (research.md §9.1, contracts/taxonomy-resolver.md).

Maps a shopper's free-text category/attribute terms (e.g. "t-shirt", "red") onto the
connected store's real, current vocabulary — via normalization + a small curated synonym
table, NEVER via an LLM call or embedding/semantic search (deliberately out of scope for
this internship deliverable, see spec.md Assumptions). This keeps resolution cheap, fast,
and fully unit-testable without any LLM provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.adapters.base import CommerceAdapter
from src.session.taxonomy_cache import TaxonomyCache, TaxonomySnapshot


class ResolutionStatus(str, Enum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    STALE = "stale"


@dataclass
class Candidate:
    id: str
    display_label: str


@dataclass
class ResolutionResult:
    status: ResolutionStatus
    resolved_id: Optional[str] = None
    candidates: list[Candidate] = field(default_factory=list)
    snapshot_age_seconds: float = 0.0


def _normalize(term: str) -> str:
    """Lowercase + basic singular/plural folding + whitespace collapse."""
    normalized = " ".join(term.strip().lower().split())
    if normalized.endswith("s") and len(normalized) > 3:
        normalized = normalized[:-1]
    return normalized


class TaxonomyResolver:
    """Deterministic term -> real-store-taxonomy resolver.

    Never consults an LLM to make its decision (contracts/taxonomy-resolver.md). Callers
    (agent/dialogue.py) MUST treat the returned `resolved_id`/`candidates` as CANDIDATES
    only — the live search_products/get_product call remains the sole source of truth for
    whether a specific product/variant combination actually exists (research.md §9.2).
    """

    def __init__(self, adapter: CommerceAdapter, cache: Optional[TaxonomyCache] = None) -> None:
        self._adapter = adapter
        self._cache = cache or TaxonomyCache()

    def _snapshot(self) -> TaxonomySnapshot:
        return self._cache.get_or_refresh(self._adapter)

    def list_category_names(self) -> list[str]:
        """Real, current category names from the cached snapshot — grounding data handed to
        the LLM (agent/dialogue.py's _build_llm_context) so vague/open-ended chat ("surprise
        me", "what departments do you have") can reference this store's genuine categories
        instead of guessing generic ones. Real, confirmed live bug: asked for an anniversary
        gift idea with no other context, the LLM suggested "jewelry, a handbag, a watch" for
        a store that sells none of those — the existing "never invent a category" rule had
        nothing real to ground a helpful suggestion in, so it improvised from world knowledge
        instead. May raise AdapterUnavailableError (same as resolve_category) — callers on a
        best-effort context-enrichment path should treat that as "nothing to add," not fail
        the turn."""
        # "Root" is PrestaShop's own hardcoded internal top-level category (every store has
        # one, never a real merchant-chosen department) — filtered here rather than at the
        # adapter's list_categories(), which other callers (resolve_category's substring
        # matching) don't need protected from, since no real shopper types "root".
        return [c.name for c in self._snapshot().categories if c.name.strip().lower() != "root"]

    def resolve_category(self, term: str) -> ResolutionResult:
        snapshot = self._snapshot()
        normalized = _normalize(term)
        if not normalized:
            return ResolutionResult(
                status=ResolutionStatus.UNSUPPORTED, snapshot_age_seconds=snapshot.age_seconds
            )

        # Apply curated synonym table first (e.g. "tee"/"tshirt" -> "t-shirts").
        normalized = snapshot.synonym_table.get(normalized, normalized)

        # Real, confirmed live bug: "Men" is a literal substring of "Women" — the substring
        # check below OR-matches both for the term "women", so a shopper typing the exact
        # category name still got an ambiguous "did you mean: Men, Women?" question, and
        # answering it with that very word ("Women") looped right back to the identical
        # question forever (it always substring-matches "Men" too, no matter how many times
        # it's repeated). This generalizes to any two category names where one is contained
        # in the other. An exact, whole-string match against a real category wins outright —
        # the same "verbatim exact name wins" precedent already used for products in
        # agent/intents.py's _resolve_single_product — checked before the looser substring
        # pass below ever gets a chance to introduce a false collision.
        exact = [c for c in snapshot.categories if _normalize(c.name) == normalized]
        if len(exact) == 1:
            return ResolutionResult(
                status=ResolutionStatus.EXACT,
                resolved_id=exact[0].id,
                snapshot_age_seconds=snapshot.age_seconds,
            )

        matches = [
            Candidate(id=c.id, display_label=c.name)
            for c in snapshot.categories
            if normalized in _normalize(c.name) or _normalize(c.name) in normalized
        ]

        return self._build_result(matches, snapshot)

    def list_descendant_category_ids(self, category_id: str) -> list[str]:
        """All descendant category ids (children, grandchildren, ...) of category_id, per
        the cached snapshot's parent_id links.

        Real, confirmed live bug: "show me clothes" (or "browse to clothes") found nothing
        at all — an umbrella category like "Clothes" often has no products directly attached
        to it, only subcategories ("Men", "Women") that hold the real products. Neither a
        category-scoped search nor a plain keyword search on the umbrella word itself
        (no product is literally named "clothes") ever finds them. Callers use this to widen
        an empty umbrella-category search to include its subcategories' real products."""
        snapshot = self._snapshot()
        children_by_parent: dict[str, list[str]] = {}
        for c in snapshot.categories:
            if c.parent_id:
                children_by_parent.setdefault(c.parent_id, []).append(c.id)
        descendants: list[str] = []
        frontier = [category_id]
        while frontier:
            children = children_by_parent.get(frontier.pop(), [])
            descendants.extend(children)
            frontier.extend(children)
        return descendants

    def resolve_attribute_value(self, attribute_group: str, term: str) -> ResolutionResult:
        snapshot = self._snapshot()
        normalized = _normalize(term)
        if not normalized:
            return ResolutionResult(
                status=ResolutionStatus.UNSUPPORTED, snapshot_age_seconds=snapshot.age_seconds
            )
        normalized = snapshot.synonym_table.get(normalized, normalized)

        group = next(
            (g for g in snapshot.attribute_groups if _normalize(g.name) == _normalize(attribute_group)),
            None,
        )
        if group is None:
            return ResolutionResult(
                status=ResolutionStatus.UNSUPPORTED, snapshot_age_seconds=snapshot.age_seconds
            )

        matches = [
            Candidate(id=value, display_label=value)
            for value in group.values
            if normalized == _normalize(value) or normalized in _normalize(value)
        ]

        return self._build_result(matches, snapshot)

    def mark_stale_and_refresh(self, term_resolution: ResolutionResult) -> ResolutionResult:
        """Call this when a live search using an `exact` resolution returned zero results
        (research.md §9.1's `stale` outcome) — triggers one re-resolution after a refresh."""
        self._cache.force_refresh(self._adapter)
        return ResolutionResult(
            status=ResolutionStatus.STALE, snapshot_age_seconds=0.0
        )

    @staticmethod
    def _build_result(matches: list[Candidate], snapshot: TaxonomySnapshot) -> ResolutionResult:
        age = snapshot.age_seconds
        if len(matches) == 1:
            return ResolutionResult(
                status=ResolutionStatus.EXACT,
                resolved_id=matches[0].id,
                snapshot_age_seconds=age,
            )
        if len(matches) > 1:
            return ResolutionResult(
                status=ResolutionStatus.AMBIGUOUS, candidates=matches, snapshot_age_seconds=age
            )
        return ResolutionResult(status=ResolutionStatus.UNSUPPORTED, snapshot_age_seconds=age)
