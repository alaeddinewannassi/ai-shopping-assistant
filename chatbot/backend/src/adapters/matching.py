"""Shared, catalog-agnostic keyword-matching helpers used by both CommerceAdapter
implementations' search_products (MockAdapter, PrestaShopAdapter) and by
agent/intents.py's strict AND-narrowing (_resolve_single_product) — one place so a fix to
one doesn't quietly drift from the other.

Deliberately equality-based (after normalizing both the query token AND the product name to
a small set of word-forms), never substring containment. An earlier version used "one word
contains the other" to handle simple cases ("posters" matching "poster", "tshirt" matching
"t-shirt") — but that same looseness let short, unrelated words silently match: "not" (as in
"I want the tshirt not the sweater") is a literal substring of "notebook", so a real query
pulled in three unrelated notebook products. Equality-after-folding still handles the
legitimate cases (see _word_forms) without matching arbitrary word prefixes — and, unlike an
earlier version of this fix, normalizes BOTH sides the same way: a query typed WITH a hyphen
("t-shirt") needs the same treatment as the catalog name, or it stops matching entirely.
"""

from __future__ import annotations

import re


def fold_plural(word: str) -> str:
    """Strips one trailing "s" from an otherwise-plausible plural — "posters" -> "poster".
    Left alone for short words (avoids "gas" -> "ga") and anything not ending in "s"."""
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def _word_forms(word: str) -> set[str]:
    """Every plural-folded form one word (from either a query token or a product name) can
    legitimately be matched by: itself, and — if hyphenated — each hyphen-separated part
    plus the hyphen-removed compound ("t-shirt" -> "shirt" AND "tshirt"). Applied identically
    to both sides of a comparison, so "t-shirt" (typed with the hyphen) and "tshirt" (typed
    without it) both still match a catalog name spelled either way. Short forms (<=2 chars)
    are dropped throughout — the same noise filter that keeps "t" from matching everything."""
    parts = [p for p in word.split("-") if p]
    forms = {p for p in parts if len(p) > 2}
    if len(parts) > 1:
        forms.add("".join(parts))
    return {fold_plural(f) for f in forms}


def name_match_forms(name: str) -> set[str]:
    """All word-forms a query token might legitimately match against this product name."""
    forms: set[str] = set()
    for word in re.sub(r"[^\w\s-]", "", name.lower()).split():
        forms |= _word_forms(word)
    return forms


def token_matches_name(token: str, name: str) -> bool:
    """True if `token` (a single query word, possibly hyphenated) legitimately matches
    `name` (a product name) — any overlap between their respective word-forms, never a loose
    substring check (see this module's docstring for why that was a real bug)."""
    return bool(_word_forms(token.lower()) & name_match_forms(name))
