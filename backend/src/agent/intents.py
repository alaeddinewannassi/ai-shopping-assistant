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

from src.adapters.base import (
    AdapterUnavailableError,
    Cart,
    CartLine,
    CommerceAdapter,
    Product,
    ProductNotFoundError,
    Variant,
)
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


# --------------------------------------------------------------------------------------- #
# User Story 2 - Add to Cart with Confirmation (T033)
# --------------------------------------------------------------------------------------- #

_QUANTITY_PATTERN = re.compile(r"(?<!\$)\b(\d+)\b")
_CART_STOPWORDS = _STOPWORDS | {
    "add", "cart", "my", "remove", "delete", "update", "change", "set", "from", "quantity",
    "qty", "in",
}


def _extract_quantity(text: str) -> int:
    match = _QUANTITY_PATTERN.search(text)
    if not match:
        return 1
    return max(1, int(match.group(1)))


def _clean_reference_term(text: str) -> str:
    without_qty = _QUANTITY_PATTERN.sub("", text)
    tokens = [
        t for t in re.sub(r"[^\w\s-]", "", without_qty).lower().split() if t not in _CART_STOPWORDS
    ]
    return " ".join(tokens)


def _value_mentioned(value: str, text_lower: str) -> bool:
    return re.search(rf"\b{re.escape(value.lower())}\b", text_lower) is not None


class CartResolutionKind(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS_PRODUCT = "ambiguous_product"
    AMBIGUOUS_VARIANT = "ambiguous_variant"
    NOT_FOUND = "not_found"
    OUT_OF_STOCK = "out_of_stock"
    UNAVAILABLE = "unavailable"
    LINE_NOT_FOUND = "line_not_found"


@dataclass
class CartResolution:
    kind: CartResolutionKind
    product: Optional[Product] = None
    variant: Optional[Variant] = None
    quantity: int = 1
    candidates: list[str] = field(default_factory=list)
    alternatives: list[Variant] = field(default_factory=list)
    line: Optional[CartLine] = None


class CartIntentHandler:
    """Resolves free-text cart requests (add/update/remove) into concrete product/variant
    references BEFORE any `PendingAction` is created (FR-019: no guessing on ambiguous
    references) — the dialogue layer (agent/dialogue.py) is responsible for turning a
    `RESOLVED` outcome into a `PendingActionGate.propose()` call, never this class directly
    (research.md §9.3: intent parsing has no mutation-adjacent capability of its own)."""

    def __init__(self, adapter: CommerceAdapter) -> None:
        self._adapter = adapter

    def resolve_add_to_cart(self, raw_text: str) -> CartResolution:
        """US2 Scenario 1 (resolve what to add) + Scenario 5 (out-of-stock)."""
        quantity = _extract_quantity(raw_text)
        term = _clean_reference_term(raw_text)

        try:
            products = self._adapter.search_products(query=term)
        except AdapterUnavailableError:
            return CartResolution(kind=CartResolutionKind.UNAVAILABLE)

        if not products:
            return CartResolution(kind=CartResolutionKind.NOT_FOUND)
        if len(products) > 1:
            return CartResolution(
                kind=CartResolutionKind.AMBIGUOUS_PRODUCT, candidates=[p.name for p in products[:5]]
            )

        product = products[0]
        variant = self._resolve_variant(product, raw_text)
        if variant is None:
            options = [
                ", ".join(f"{k}: {v}" for k, v in variant_.attributes.items())
                for variant_ in product.variants
            ]
            return CartResolution(
                kind=CartResolutionKind.AMBIGUOUS_VARIANT, product=product, candidates=options
            )

        if not variant.in_stock or variant.stock_quantity < quantity:
            alternatives = [v for v in product.variants if v.in_stock and v.id != variant.id]
            return CartResolution(
                kind=CartResolutionKind.OUT_OF_STOCK,
                product=product,
                variant=variant,
                alternatives=alternatives,
            )

        return CartResolution(
            kind=CartResolutionKind.RESOLVED, product=product, variant=variant, quantity=quantity
        )

    def resolve_cart_line_reference(self, cart: Cart, raw_text: str) -> CartResolution:
        """US2 Scenario 4 (update quantity / remove an existing line): finds the single cart
        line the shopper is referring to by product name, without ever guessing among
        several matches."""
        text_lower = raw_text.lower()
        matches: list[tuple[CartLine, Product]] = []
        for line in cart.lines:
            try:
                product = self._adapter.get_product(line.product_id)
            except AdapterUnavailableError:
                return CartResolution(kind=CartResolutionKind.UNAVAILABLE)
            except ProductNotFoundError:
                continue
            name_tokens = [t for t in product.name.lower().split() if len(t) > 2]
            if any(_value_mentioned(t, text_lower) for t in name_tokens):
                matches.append((line, product))

        if len(matches) == 1:
            line, _product = matches[0]
            return CartResolution(kind=CartResolutionKind.RESOLVED, line=line, quantity=_extract_quantity(raw_text))
        if len(matches) > 1:
            return CartResolution(
                kind=CartResolutionKind.AMBIGUOUS_PRODUCT,
                candidates=[p.name for _l, p in matches],
            )
        return CartResolution(kind=CartResolutionKind.LINE_NOT_FOUND)

    @staticmethod
    def _resolve_variant(product: Product, raw_text: str) -> Optional[Variant]:
        if len(product.variants) == 1:
            return product.variants[0]

        text_lower = raw_text.lower()
        # Only constrain on attributes the shopper actually mentioned (e.g. just a color,
        # with no size) — requiring every attribute to be spelled out would make the common
        # "add the red t-shirt" phrasing spuriously ambiguous.
        all_values_by_attr: dict[str, set[str]] = {}
        for v in product.variants:
            for attr_name, value in v.attributes.items():
                all_values_by_attr.setdefault(attr_name, set()).add(value)

        mentioned: dict[str, str] = {}
        for attr_name, values in all_values_by_attr.items():
            for value in values:
                if _value_mentioned(value, text_lower):
                    mentioned[attr_name] = value
                    break

        if not mentioned:
            return None

        candidates = [
            v for v in product.variants if all(v.attributes.get(k) == val for k, val in mentioned.items())
        ]
        if len(candidates) == 1:
            return candidates[0]
        return None
