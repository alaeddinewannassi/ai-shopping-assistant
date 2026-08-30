"""Redis key namespacing per tenant (T204).

SessionStore/CatalogSnapshotCache accept an optional `key_prefix` so multiple tenants can
share one Redis instance without their keys colliding. The legacy/default tenant keeps an
empty prefix (unprefixed keys) so an in-place upgrade doesn't orphan its live sessions.
"""

from __future__ import annotations

from src.session.catalog_cache import CatalogSnapshotCache
from src.session.store import SessionStore


def test_session_store_key_is_unprefixed_by_default() -> None:
    store = SessionStore(redis_url=None)
    assert store._key("abc") == "session:abc"


def test_session_store_key_is_namespaced_per_tenant() -> None:
    store = SessionStore(redis_url=None, key_prefix="t:store-a:")
    assert store._key("abc") == "t:store-a:session:abc"


def test_two_tenants_never_produce_the_same_session_key_for_the_same_session_id() -> None:
    store_a = SessionStore(redis_url=None, key_prefix="t:store-a:")
    store_b = SessionStore(redis_url=None, key_prefix="t:store-b:")
    assert store_a._key("shared-session-id") != store_b._key("shared-session-id")


def test_catalog_cache_key_is_namespaced_per_tenant() -> None:
    cache = CatalogSnapshotCache(redis_url=None, key_prefix="t:store-a:")
    assert cache._key("category:5") == "t:store-a:catalog_snapshot:category:5"
