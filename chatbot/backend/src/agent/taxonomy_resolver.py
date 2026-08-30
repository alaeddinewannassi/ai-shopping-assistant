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

    def resolve_category(self, term: str) -> ResolutionResult:
        snapshot = self._snapshot()
        normalized = _normalize(term)
        if not normalized:
            return ResolutionResult(
                status=ResolutionStatus.UNSUPPORTED, snapshot_age_seconds=snapshot.age_seconds
            )

        # Apply curated synonym table first (e.g. "tee"/"tshirt" -> "t-shirts").
        normalized = snapshot.synonym_table.get(normalized, normalized)

        matches = [
            Candidate(id=c.id, display_label=c.name)
            for c in snapshot.categories
            if normalized in _normalize(c.name) or _normalize(c.name) in normalized
        ]

        return self._build_result(matches, snapshot)

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
