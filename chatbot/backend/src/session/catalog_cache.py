"""Redis-backed CatalogSnapshot cache (data-model.md, T012a).

Read-only fallback cache used ONLY when the CommerceAdapter raises AdapterUnavailableError
for a discovery/navigation call (research.md §8, FR-016). This module MUST NOT be imported
by any cart/promo/checkout code path — enforced by test_no_cache_imports_in_mutation_paths
in tests/unit.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover
    redis = None  # type: ignore

DEFAULT_TTL_SECONDS = int(os.environ.get("CATALOG_SNAPSHOT_TTL_SECONDS", "180"))


@dataclass
class CatalogSnapshotEntry:
    cache_key: str
    products: list[dict] = field(default_factory=list)  # serialized Product dicts
    fetched_at: float = field(default_factory=time.time)
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.fetched_at) > self.ttl_seconds


class CatalogSnapshotCache:
    """Ephemeral, short-TTL, read-only cache. Never consulted for cart/promo/order decisions."""

    def __init__(
        self,
        redis_url: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        *,
        key_prefix: str = "",
    ) -> None:
        # key_prefix namespaces Redis keys per tenant (T204) — empty for the legacy
        # single-tenant/default deployment so its existing keys are unaffected.
        self._key_prefix = key_prefix
        self._ttl = ttl_seconds
        self._client = None
        redis_url = redis_url or os.environ.get("REDIS_URL")
        if redis is not None and redis_url:
            try:
                self._client = redis.from_url(redis_url, decode_responses=True)
                self._client.ping()
            except Exception:  # noqa: BLE001
                self._client = None
        self._memory: dict[str, CatalogSnapshotEntry] = {}

    def _key(self, cache_key: str) -> str:
        return f"{self._key_prefix}catalog_snapshot:{cache_key}"

    def put(self, cache_key: str, products: list[dict]) -> None:
        entry = CatalogSnapshotEntry(cache_key=cache_key, products=products, ttl_seconds=self._ttl)
        if self._client is not None:
            self._client.set(self._key(cache_key), json.dumps(asdict(entry)), ex=self._ttl)
        else:
            self._memory[cache_key] = entry

    def get(self, cache_key: str) -> CatalogSnapshotEntry | None:
        """Returns None if nothing cached OR the cached entry has expired — expired entries
        are treated as absent, never served (data-model.md CatalogSnapshot)."""
        if self._client is not None:
            raw = self._client.get(self._key(cache_key))
            if raw is None:
                return None
            entry = CatalogSnapshotEntry(**json.loads(raw))
        else:
            entry = self._memory.get(cache_key)
            if entry is None:
                return None

        if entry.is_expired:
            return None
        return entry
