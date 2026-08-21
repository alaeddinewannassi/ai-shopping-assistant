"""Discovery/navigation intent handling for User Story 1 (T022, T023, T025a, T026).

Turns a shopper's free-text search/navigate request into a `DiscoveryOutcome`: grounds any
category/attribute term against the `TaxonomyResolver` (research.md §9) — never inventing a
category id — asks at most one clarifying question when a term is ambiguous (FR-003), and
falls back to the read-only `CatalogSnapshotCache` with a "may be outdated" disclaimer when
the live adapter is unreachable (FR-016, research.md §8), never fabricating product data.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional

from src.adapters.base import AdapterUnavailableError, CommerceAdapter, Product, Variant
from src.agent.taxonomy_resolver import Candidate, ResolutionStatus, TaxonomyResolver
from src.session.catalog_cache import CatalogSnapshotCache

_PRICE_PATTERN = re.compile(r"under\s+\$?(\d+(?:\.\d+)?)|below\s+\$?(\d+(?:\.\d+)?)", re.I)
_NAVIGATION_PATTERN = re.compile(
    r"\b(?:take me to|go to|navigate to|show me the)\b\s+(?:the\s+)?(.+)", re.I
)
_STOPWORDS = {
    "show", "me", "the", "a", "an", "please", "for", "with", "under", "over", "some", "any",
    "find", "search", "looking", "want", "need", "to", "category", "page", "products",
    "product", "of",
}


class DiscoveryKind(str, Enum):
    PRODUCTS = "products"
    NAVIGATE_CATEGORY = "navigate_category"
    CLARIFY = "clarify"
    NO_MATCH = "no_match"
    UNAVAILABLE = "unavailable"


@dataclass
class DiscoveryOutcome:
    kind: DiscoveryKind
    products: list[Product] = field(default_factory=list)
    category: Optional[Candidate] = None
    clarifying_options: list[Candidate] = field(default_factory=list)
    degraded: bool = False


def _extract_price_ceiling(text: str) -> Optional[float]:
    match = _PRICE_PATTERN.search(text)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return float(value)


def _strip_price_clause(text: str) -> str:
    return _PRICE_PATTERN.sub("", text).strip()


def _clean_term(text: str) -> str:
    tokens = [t for t in re.sub(r"[^\w\s-]", "", text).lower().split() if t not in _STOPWORDS]
    return " ".join(tokens)


def _rehydrate(raw_products: list[dict]) -> list[Product]:
    """Reconstructs Product/Variant dataclasses from the JSON-serializable dicts stored in
    CatalogSnapshotCache (see CatalogSnapshotCache.put)."""
    result = []
    for p in raw_products:
        variants = [Variant(**v) for v in p.get("variants", [])]
        result.append(
            Product(
                id=p["id"],
                name=p["name"],
                category_id=p["category_id"],
                base_price=p["base_price"],
                variants=variants,
            )
        )
    return result


class DiscoveryIntentHandler:
    """Implements T022 (search_products/navigate_to), T023 (ambiguity), T025a (degraded mode),
    T026 (empty-result handling)."""

    def __init__(
        self,
        adapter: CommerceAdapter,
        resolver: TaxonomyResolver,
        catalog_cache: Optional[CatalogSnapshotCache] = None,
    ) -> None:
        self._adapter = adapter
        self._resolver = resolver
        self._cache = catalog_cache or CatalogSnapshotCache()

    def handle_search(self, raw_text: str) -> DiscoveryOutcome:
        """Scenario 1 (category+constraint search), Scenario 3 (ambiguity), Scenario 4
        (no-match), and the backend-unreachable edge case (T021a)."""
        max_price = _extract_price_ceiling(raw_text)
        term = _clean_term(_strip_price_clause(raw_text))

        filters: dict = {}
        if max_price is not None:
            filters["max_price"] = max_price

        category_result = None
        if term:
            try:
                category_result = self._resolver.resolve_category(term)
            except AdapterUnavailableError:
                # TaxonomyResolver needed a fresh snapshot (none cached yet) and the store
                # is unreachable — fall back the same way a failed live search would
                # (research.md §8/§9.2), never silently proceeding with a guessed filter.
                return self._degraded_or_unavailable(f"search:{term}:{max_price}")
        if category_result is not None and category_result.status == ResolutionStatus.AMBIGUOUS:
            return DiscoveryOutcome(
                kind=DiscoveryKind.CLARIFY, clarifying_options=category_result.candidates
            )
        query = term
        if category_result is not None and category_result.status == ResolutionStatus.EXACT:
            filters["category_id"] = category_result.resolved_id
            query = ""

        return self._run_search(cache_key=f"search:{term}:{max_price}", query=query, filters=filters)

    def handle_navigate(self, raw_text: str) -> DiscoveryOutcome:
        """Scenario 2 (navigate to a named category/product)."""
        match = _NAVIGATION_PATTERN.search(raw_text)
        target_phrase = match.group(1) if match else raw_text
        term = _clean_term(target_phrase)

        try:
            category_result = self._resolver.resolve_category(term)
        except AdapterUnavailableError:
            return self._degraded_or_unavailable(f"nav:{term}")
        if category_result.status == ResolutionStatus.AMBIGUOUS:
            return DiscoveryOutcome(
                kind=DiscoveryKind.CLARIFY, clarifying_options=category_result.candidates
            )
        if category_result.status == ResolutionStatus.EXACT:
            # The cached taxonomy snapshot is candidates-only — the live read remains the
            # authoritative source of whether the category still actually has data
            # (research.md §9.2).
            try:
                products = self._adapter.search_products(
                    filters={"category_id": category_result.resolved_id}
                )
            except AdapterUnavailableError:
                return self._degraded_or_unavailable(f"nav:{term}")
            self._cache.put(f"nav:{term}", [asdict(p) for p in products])
            return DiscoveryOutcome(
                kind=DiscoveryKind.NAVIGATE_CATEGORY,
                category=Candidate(id=category_result.resolved_id, display_label=term),
                products=products,
            )

        # Not a known category — try treating the phrase as a specific product name.
        return self._run_search(cache_key=f"nav:{term}", query=term, filters={})

    def _run_search(self, *, cache_key: str, query: str, filters: dict) -> DiscoveryOutcome:
        try:
            products = self._adapter.search_products(query=query, filters=filters)
        except AdapterUnavailableError:
            return self._degraded_or_unavailable(cache_key)

        self._cache.put(cache_key, [asdict(p) for p in products])
        if not products:
            return DiscoveryOutcome(kind=DiscoveryKind.NO_MATCH)
        return DiscoveryOutcome(kind=DiscoveryKind.PRODUCTS, products=products)

    def _degraded_or_unavailable(self, cache_key: str) -> DiscoveryOutcome:
        entry = self._cache.get(cache_key)
        if entry is None:
            return DiscoveryOutcome(kind=DiscoveryKind.UNAVAILABLE)
        return DiscoveryOutcome(
            kind=DiscoveryKind.PRODUCTS, products=_rehydrate(entry.products), degraded=True
        )
