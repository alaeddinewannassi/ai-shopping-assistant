"""Discovery/navigation intent handling for User Story 1 (T022, T023, T025a, T026).

Turns a shopper's free-text search/navigate request into a `DiscoveryOutcome`: grounds any
category/attribute term against the `TaxonomyResolver` (research.md §9) — never inventing a
category id — asks at most one clarifying question when a term is ambiguous (FR-003), and
falls back to the read-only `CatalogSnapshotCache` with a "may be outdated" disclaimer when
the live adapter is unreachable (FR-016, research.md §8), never fabricating product data.
"""

from __future__ import annotations

import logging
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
    PromoValidation,
    Variant,
)
from src.adapters.matching import token_matches_product
from src.agent.taxonomy_resolver import Candidate, ResolutionStatus, TaxonomyResolver
from src.session.catalog_cache import CatalogSnapshotCache

# The shopper-facing "can't reach the store's catalog" reply (dialogue.py) is deliberately
# generic — a real customer during a real outage should never see internal config guidance.
# This logger is where that detail actually goes, for whoever is operating the store: the
# adapter's own exception message distinguishes "never configured" (e.g. missing/invalid
# webservice key) from "temporarily unreachable" (e.g. timeout) far better than the
# generic AdapterUnavailableError type alone does.
_logger = logging.getLogger("assistant.adapter")


def _log_unavailable(where: str, exc: Exception) -> None:
    _logger.warning("Adapter unavailable during %s: %s", where, exc)


_PRICE_PATTERN = re.compile(r"under\s+\$?(\d+(?:\.\d+)?)|below\s+\$?(\d+(?:\.\d+)?)", re.I)
_NAVIGATION_PATTERN = re.compile(
    r"\b(?:take me to|go to|navigate to|show me the)\b\s+(?:the\s+)?(.+)", re.I
)
_STOPWORDS = {
    "show", "me", "the", "a", "an", "please", "for", "with", "under", "over", "some", "any",
    "find", "search", "looking", "want", "need", "to", "category", "page", "products",
    "product", "of",
    # Filler words from a general "what do you have?" phrasing — without these, a leftover
    # word like "available" gets treated as a real search keyword and matched (or not)
    "what", "are", "is", "available", "have", "has", "got", "all", "everything", "your",
    "you", "do", "does", "sell", "carry", "stock", "offer",
    # Common acknowledgment fillers ("ok add me one...") — not identifying words either.
    "ok", "okay", "sure", "yeah", "yep", "alright",
}


class DiscoveryKind(str, Enum):
    PRODUCTS = "products"
    NAVIGATE_CATEGORY = "navigate_category"
    CLARIFY = "clarify"
    NO_MATCH = "no_match"
    UNAVAILABLE = "unavailable"
    PRODUCT_DETAILS = "product_details"


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
            except AdapterUnavailableError as exc:
                # TaxonomyResolver needed a fresh snapshot (none cached yet) and the store
                # is unreachable — fall back the same way a failed live search would
                # (research.md §8/§9.2), never silently proceeding with a guessed filter.
                _log_unavailable(f"search:{term}", exc)
                return self._degraded_or_unavailable(f"search:{term}:{max_price}")
        if category_result is not None and category_result.status == ResolutionStatus.AMBIGUOUS:
            return DiscoveryOutcome(
                kind=DiscoveryKind.CLARIFY, clarifying_options=category_result.candidates
            )
        if category_result is not None and category_result.status == ResolutionStatus.EXACT:
            category_outcome = self._run_search(
                cache_key=f"search:{term}:{max_price}:cat{category_result.resolved_id}",
                query="",
                filters={**filters, "category_id": category_result.resolved_id},
            )
            if category_outcome.kind != DiscoveryKind.NO_MATCH:
                return category_outcome
            # The resolver's category match is a loose substring check (see
            # taxonomy_resolver._normalize) — fine for a short category term ("t-shirt"),
            # but a full sentence that merely mentions a category word in passing ("she
            # likes clothes, maybe a t-shirt") can "exact"-match an umbrella category with
            # no directly-attached products. An empty category shouldn't shadow a real
            # keyword match sitting elsewhere in the catalog — fall back to plain search.

        return self._run_search(cache_key=f"search:{term}:{max_price}", query=term, filters=filters)

    def handle_navigate(self, raw_text: str) -> DiscoveryOutcome:
        """Scenario 2 (navigate to a named category/product)."""
        match = _NAVIGATION_PATTERN.search(raw_text)
        target_phrase = match.group(1) if match else raw_text
        term = _clean_term(target_phrase)

        try:
            category_result = self._resolver.resolve_category(term)
        except AdapterUnavailableError as exc:
            _log_unavailable(f"navigate:{term}", exc)
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
            except AdapterUnavailableError as exc:
                _log_unavailable(f"navigate:{term}", exc)
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
        except AdapterUnavailableError as exc:
            _log_unavailable(cache_key, exc)
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

    def resolve_product_details(
        self, raw_text: str, last_shown_ids: list[str] | None = None
    ) -> DiscoveryOutcome:
        """The shopper is asking about a SPECIFIC, already-discussed product's real
        attributes (sizes/colors/stock) — e.g. "what sizes do you have", "is it in stock".
        Resolved via the same reference/keyword logic as
        CartIntentHandler.resolve_add_to_cart (_resolve_single_product, defined in the User
        Story 2 section below but shared across both), but strictly read-only: this never
        proposes anything, it only reports real facts already in the catalog."""
        try:
            product, candidates = _resolve_single_product(self._adapter, raw_text, last_shown_ids)
        except AdapterUnavailableError as exc:
            _log_unavailable(f"product_details:{raw_text}", exc)
            return DiscoveryOutcome(kind=DiscoveryKind.UNAVAILABLE)

        if product is None:
            if candidates:
                return DiscoveryOutcome(
                    kind=DiscoveryKind.CLARIFY,
                    clarifying_options=[
                        Candidate(id=p.id, display_label=p.name) for p in candidates
                    ],
                )
            return DiscoveryOutcome(kind=DiscoveryKind.NO_MATCH)

        return DiscoveryOutcome(kind=DiscoveryKind.PRODUCT_DETAILS, products=[product])


# --------------------------------------------------------------------------------------- #
# User Story 2 - Add to Cart with Confirmation (T033)
# --------------------------------------------------------------------------------------- #

_QUANTITY_PATTERN = re.compile(r"(?<!\$)\b(\d+)\b")
_CART_STOPWORDS = _STOPWORDS | {
    "add", "cart", "card", "my", "remove", "delete", "update", "change", "set", "from",
    "quantity", "qty", "in",
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


_ORDINAL_WORDS = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}
_ORDINAL_PATTERN = re.compile(r"\b(first|second|third|fourth|fifth|last)\b", re.IGNORECASE)
# A bare pronoun with no distinguishing words of its own ("add it", "add that one") only
# resolves unambiguously when exactly one product was last shown — with several, "it" alone
# genuinely doesn't say which one, and guessing would be worse than asking.
_BARE_REFERENCE_TERMS = {"", "it", "that", "this", "one", "that one", "this one"}


def _resolve_reference_to_last_shown(raw_text: str, last_shown_ids: list[str]) -> str | None:
    """Resolves a pronoun ("add it") or ordinal ("the second one") reference against the
    product ids most recently shown to this shopper (ConversationSession.last_shown_product_ids)
    — returns that product's id, or None if raw_text isn't this kind of reference, or there's
    nothing recent to resolve it against."""
    if not last_shown_ids:
        return None

    ordinal_match = _ORDINAL_PATTERN.search(raw_text.lower())
    if ordinal_match:
        word = ordinal_match.group(1)
        index = len(last_shown_ids) - 1 if word == "last" else _ORDINAL_WORDS[word]
        return last_shown_ids[index] if 0 <= index < len(last_shown_ids) else None

    if _clean_reference_term(raw_text) in _BARE_REFERENCE_TERMS:
        return last_shown_ids[0] if len(last_shown_ids) == 1 else None

    return None


def _resolve_single_product(
    adapter: CommerceAdapter, raw_text: str, last_shown_ids: list[str] | None
) -> tuple[Optional[Product], list[Product]]:
    """Resolves raw_text to exactly one product — shared by CartIntentHandler.resolve_add_to_cart
    and DiscoveryIntentHandler.resolve_product_details, so "add it"/"the second one" and "what
    sizes does it have" resolve identically. Tries a last-shown pronoun/ordinal reference first,
    then a keyword search narrowed to require every meaningful token to match (naming an exact
    full name shouldn't go ambiguous just because every candidate shares one common word).

    Returns (product, ambiguous_candidates) — product is None when nothing matched (both empty)
    or genuinely ambiguous (candidates populated, capped at 5). Does NOT catch
    AdapterUnavailableError — that's each caller's own context to log distinctly."""
    referenced_id = _resolve_reference_to_last_shown(raw_text, last_shown_ids or [])
    if referenced_id is not None:
        try:
            return adapter.get_product(referenced_id), []
        except ProductNotFoundError:
            pass  # referenced product has since disappeared — fall through to keyword search

    term = _clean_reference_term(raw_text)
    # Both CommerceAdapter implementations treat a query with no token longer than 2 chars as
    # "nothing meaningful to filter on" and deliberately return the WHOLE catalog unfiltered
    # (reasonable for a bare discovery browse — "show me what you have" shouldn't return
    # zero results). But this same search is also how a bare/short cart reference term
    # ("ok go for it" -> "go it" after stopword-cleaning, or a lone size letter like "L")
    # gets resolved — and a real, confirmed live bug proved that "the whole catalog" is
    # exactly wrong there: it defeats the `if not products` fallback below (a genuinely
    # unmatched term should fall back to the single last-shown/pending-variant product, not
    # surface an unrelated grab-bag of the first few catalog products as "ambiguous"). Skip
    # the call entirely when the term itself carries no real search signal, using the same
    # length-2 threshold each adapter already applies internally.
    has_meaningful_token = any(len(t) > 2 for t in term.split())
    products = adapter.search_products(query=term) if has_meaningful_token else []
    if not products:
        # A pronoun combined with a variant descriptor ("add me one in size M") can leave
        # nothing but filler/quantity/variant words after stopword-cleaning ("one size m") —
        # none of which match any product name, so the keyword search above genuinely finds
        # nothing. With exactly one product just shown, that's still almost certainly what's
        # meant; a wrong guess here proposes the wrong item by name and the shopper declines
        # it (Constitution Principle III's confirm-gate), it never silently mutates anything.
        if last_shown_ids and len(last_shown_ids) == 1:
            try:
                return adapter.get_product(last_shown_ids[0]), []
            except ProductNotFoundError:
                pass
        return None, []
    if len(products) > 1:
        # "I want the tshirt not the jacket" — "not" itself correctly matches no product
        # (token_matches_name, not a loose substring check — "not" is deliberately never a
        # false match for "notebook" anymore). But requiring EVERY token to match, "not"
        # included, then means NOTHING can ever satisfy all of them: "not" needs to exclude
        # whatever follows it, not need its own product-name match. Split on it: words
        # before "not" must all match (the AND-narrowing below); words after it must match
        # NONE of a candidate (an explicit exclusion), so "the jacket" actually rules out
        # Blue Jacket instead of just failing to positively identify anything.
        term_lower = f" {term.lower()} "
        if " not " in term_lower:
            positive_part, _, negative_part = term_lower.partition(" not ")
        else:
            positive_part, negative_part = term_lower, ""
        positive_tokens = [t for t in positive_part.split() if len(t) > 2]
        negative_tokens = [t for t in negative_part.split() if len(t) > 2]
        if positive_tokens or negative_tokens:
            # token_matches_product — the SAME matching the initial broad search above
            # already used (name AND description) — not a naive substring check: "tshirt"
            # (no hyphen) legitimately matches a catalog name spelled "t-shirt", but a naive
            # `"tshirt" in name.lower()` check missed that (the hyphen makes them different
            # strings), so this narrowing step failed to narrow even when the broad search
            # above had already found the exact right product — leaving it stuck "ambiguous"
            # against unrelated candidates. Must also check description here, not just name:
            # a product the broad search above matched via its description would otherwise
            # get wrongly excluded by a stricter, name-only narrowing step.
            narrowed = [
                p for p in products
                if all(token_matches_product(t, p.name, p.description) for t in positive_tokens)
                and not any(token_matches_product(t, p.name, p.description) for t in negative_tokens)
            ]
            if len(narrowed) == 1:
                products = narrowed
    if len(products) > 1:
        return None, products[:5]
    return products[0], []


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

    def resolve_add_to_cart(
        self, raw_text: str, last_shown_ids: list[str] | None = None
    ) -> CartResolution:
        """US2 Scenario 1 (resolve what to add) + Scenario 5 (out-of-stock).

        `last_shown_ids` (session.last_shown_product_ids, dialogue.py's own record of what
        this shopper was just shown) lets a bare pronoun ("add it") or ordinal ("the second
        one") resolve against that instead of falling through to a fresh keyword search that
        has no idea what "it" refers to."""
        quantity = _extract_quantity(raw_text)
        try:
            product, candidates = _resolve_single_product(self._adapter, raw_text, last_shown_ids)
        except AdapterUnavailableError as exc:
            _log_unavailable(f"add_to_cart:{raw_text}", exc)
            return CartResolution(kind=CartResolutionKind.UNAVAILABLE)

        if product is None:
            if candidates:
                return CartResolution(
                    kind=CartResolutionKind.AMBIGUOUS_PRODUCT,
                    candidates=[p.name for p in candidates],
                )
            return CartResolution(kind=CartResolutionKind.NOT_FOUND)

        return self._resolve_variant_and_stock(product, raw_text, quantity)

    def _resolve_variant_and_stock(
        self, product: Product, raw_text: str, quantity: int
    ) -> CartResolution:
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
            except AdapterUnavailableError as exc:
                _log_unavailable(f"cart_line_reference:{line.product_id}", exc)
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


# --------------------------------------------------------------------------- #
# User Story 4 - promo code intent resolution (T059, T060)
# --------------------------------------------------------------------------- #

_PROMO_CODE_PATTERN = re.compile(r"\b([A-Za-z]+\d+)\b")


class PromoResolutionKind(str, Enum):
    RESOLVED = "resolved"
    INVALID = "invalid"
    NO_CODE_GIVEN = "no_code_given"
    UNAVAILABLE = "unavailable"


@dataclass
class PromoResolution:
    kind: PromoResolutionKind
    code: Optional[str] = None
    validation: Optional[PromoValidation] = None


class PromoIntentHandler:
    """Resolves a shopper's promo-code turn — either a manually-provided code (T060) or a
    shopper accepting a proactively-suggested one — the same way in both cases: straight to
    `adapter.validate_promo()` (contracts/promo-strategy.md "Manually-provided codes"). The
    engine (`promo/engine.py`) is never consulted here; it only drives proactive
    suggestions (T058), which are surfaced as plain text, not through this handler."""

    def __init__(self, adapter: CommerceAdapter) -> None:
        self._adapter = adapter

    def resolve_apply_promo(self, cart_id: str, raw_text: str) -> PromoResolution:
        match = _PROMO_CODE_PATTERN.search(raw_text)
        if match is None:
            return PromoResolution(kind=PromoResolutionKind.NO_CODE_GIVEN)

        code = match.group(1).upper()
        try:
            validation = self._adapter.validate_promo(cart_id, code)
        except AdapterUnavailableError as exc:
            _log_unavailable(f"apply_promo:{code}", exc)
            return PromoResolution(kind=PromoResolutionKind.UNAVAILABLE)

        if not validation.valid:
            return PromoResolution(kind=PromoResolutionKind.INVALID, code=code, validation=validation)
        return PromoResolution(kind=PromoResolutionKind.RESOLVED, code=code, validation=validation)
