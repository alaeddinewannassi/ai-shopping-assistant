"""Redis-backed TaxonomySnapshot cache (data-model.md, research.md §9.1-9.2).

Distinct from catalog_cache.py (that one is outage-only fallback). This cache exists for
NORMAL-OPERATION grounding of free-text terms against the store's real vocabulary
(categories, attribute groups/values), regardless of whether the adapter is reachable.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

from src.adapters.base import AttributeGroup, Category, CommerceAdapter

DEFAULT_TTL_SECONDS = int(os.environ.get("TAXONOMY_SNAPSHOT_TTL_SECONDS", "300"))


@dataclass
class TaxonomySnapshot:
    categories: list[Category] = field(default_factory=list)
    attribute_groups: list[AttributeGroup] = field(default_factory=list)
    synonym_table: dict[str, str] = field(default_factory=dict)
    fetched_at: float = field(default_factory=time.time)
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    @property
    def age_seconds(self) -> float:
        return time.time() - self.fetched_at

    @property
    def is_expired(self) -> bool:
        return self.age_seconds > self.ttl_seconds


# Small curated per-store alias table (research.md §9.1). This is deterministic, editable
# config — not learned/inferred by the LLM. Extend this as real store vocabulary is known.
DEFAULT_SYNONYM_TABLE: dict[str, str] = {
    "tee": "t-shirts",
    "tees": "t-shirts",
    "tshirt": "t-shirts",
    "tshirts": "t-shirts",
    "t shirt": "t-shirts",
    "t shirts": "t-shirts",
}


class TaxonomyCache:
    """In-process cache (single-process demo scope; swap for Redis-backed storage the same
    way session/store.py does, if multi-process deployment is needed later)."""

    def __init__(self) -> None:
        self._snapshot: Optional[TaxonomySnapshot] = None

    def get_or_refresh(self, adapter: CommerceAdapter) -> TaxonomySnapshot:
        if self._snapshot is None or self._snapshot.is_expired:
            self._snapshot = self._refresh(adapter)
        return self._snapshot

    def force_refresh(self, adapter: CommerceAdapter) -> TaxonomySnapshot:
        """Used when a resolution comes back `stale` (research.md §9.1) — an exact match
        that a live search then failed to confirm triggers an immediate re-fetch."""
        self._snapshot = self._refresh(adapter)
        return self._snapshot

    def _refresh(self, adapter: CommerceAdapter) -> TaxonomySnapshot:
        categories = adapter.list_categories()
        attribute_groups = adapter.list_attributes()
        return TaxonomySnapshot(
            categories=categories,
            attribute_groups=attribute_groups,
            synonym_table=dict(DEFAULT_SYNONYM_TABLE),
        )
