"""In-process FaqEntry cache — same shape as taxonomy_cache.py's TaxonomySnapshot/
TaxonomyCache, for the same reason: `adapter.list_faqs()` is real, admin-authored data that
changes rarely, so paying a live PrestaShop round-trip on every single turn would be wasted
latency for content that's effectively static within a short window.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from src.adapters.base import CommerceAdapter, FaqEntry

DEFAULT_TTL_SECONDS = int(os.environ.get("FAQ_SNAPSHOT_TTL_SECONDS", "300"))


@dataclass
class FaqSnapshot:
    entries: list[FaqEntry] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.fetched_at) > self.ttl_seconds


class FaqCache:
    """In-process cache (single-process demo scope, same as TaxonomyCache)."""

    def __init__(self) -> None:
        self._snapshot: FaqSnapshot | None = None

    def get_or_refresh(self, adapter: CommerceAdapter) -> FaqSnapshot:
        if self._snapshot is None or self._snapshot.is_expired:
            self._snapshot = FaqSnapshot(entries=adapter.list_faqs())
        return self._snapshot
