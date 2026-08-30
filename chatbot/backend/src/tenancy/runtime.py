"""Per-tenant runtime pool — the T203 replacement for the module-level `_adapter` /
`_session_store` / `_dialogue_ctx` singletons `src/api/chat.py` used to build once at
import time from process env vars.

Each tenant gets its own `CommerceAdapter` instance (own `httpx.Client`, own
`CircuitBreaker` — one tenant's broken store must never trip another's, plan.md D3), its
own `LLMClient`, and its own Redis-namespaced `SessionStore`/`CatalogSnapshotCache` (T204).
Built lazily on first use per tenant and cached with a short TTL so a chat turn doesn't pay
a fresh adapter-construction cost (and, on the DB path, a repository round-trip) every turn;
`invalidate()` drops a tenant's cached runtime immediately after an admin config change.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from src.adapters.base import CommerceAdapter
from src.agent.dialogue import DialogueContext
from src.agent.intents import CartIntentHandler, DiscoveryIntentHandler, PromoIntentHandler
from src.agent.llm_client import LLMClient, RuleBasedStubClient, create_llm_client
from src.agent.pending import PendingActionGate
from src.agent.taxonomy_resolver import TaxonomyResolver
from src.session.catalog_cache import CatalogSnapshotCache
from src.session.store import SessionStore
from src.tenancy.config import TenantConfig

_RUNTIME_TTL_SECONDS = 60.0


@dataclass
class TenantRuntime:
    config: TenantConfig
    adapter: CommerceAdapter
    session_store: SessionStore
    dialogue_ctx: DialogueContext
    built_at: float


def _build_adapter(config: TenantConfig) -> CommerceAdapter:
    if config.adapter_platform == "prestashop":
        from src.adapters.prestashop import PrestaShopAdapter

        return PrestaShopAdapter(
            base_url=config.adapter_base_url,
            api_key=config.adapter_api_key,
            lang_id=config.adapter_lang_id,
            host_header=config.adapter_host_header,
            default_customer_id=config.default_customer_id,
            default_address_id=config.default_address_id,
            default_carrier_id=config.default_carrier_id,
            default_currency_id=config.default_currency_id,
            default_order_state_id=config.default_order_state_id,
            payment_module=config.payment_module,
            payment_label=config.payment_label,
        )
    from src.adapters.mock import MockAdapter

    return MockAdapter()


def _build_llm_client(config: TenantConfig) -> LLMClient:
    if config.llm_provider == "rule-based-stub":
        return RuleBasedStubClient()
    try:
        return create_llm_client(
            provider=config.llm_provider, api_key=config.llm_api_key, model=config.llm_model
        )
    except ValueError:
        # Configured provider can't be built (e.g. missing api key) — never let one
        # tenant's misconfiguration take down its ability to converse at all.
        return RuleBasedStubClient()


def build_tenant_runtime(config: TenantConfig) -> TenantRuntime:
    """Constructs a fresh, fully-wired TenantRuntime for one tenant — no caching here;
    caching/TTL is `get_or_build_runtime`'s job."""
    adapter = _build_adapter(config)
    llm_client = _build_llm_client(config)

    # Legacy/default tenant keeps today's unprefixed Redis keys (`session:...`,
    # `catalog_snapshot:...`) so an in-flight deployment upgrading to this feature doesn't
    # orphan its live sessions; every other tenant is namespaced (T204).
    key_prefix = "" if config.slug == _default_tenant_slug() else f"t:{config.slug}:"

    session_store = SessionStore(key_prefix=key_prefix)
    catalog_cache = CatalogSnapshotCache(key_prefix=key_prefix)
    pending_gate = PendingActionGate(session_store, adapter)
    taxonomy_resolver = TaxonomyResolver(adapter)
    discovery_handler = DiscoveryIntentHandler(adapter, taxonomy_resolver, catalog_cache)
    cart_handler = CartIntentHandler(adapter)
    promo_handler = PromoIntentHandler(adapter)

    dialogue_ctx = DialogueContext(
        session_store=session_store,
        llm_client=llm_client,
        discovery_handler=discovery_handler,
        adapter=adapter,
        cart_handler=cart_handler,
        pending_gate=pending_gate,
        promo_handler=promo_handler,
        promo_rules=config.promo_rules,
        tenant_id=config.tenant_id,
    )
    return TenantRuntime(
        config=config,
        adapter=adapter,
        session_store=session_store,
        dialogue_ctx=dialogue_ctx,
        built_at=time.monotonic(),
    )


def _default_tenant_slug() -> str:
    import os

    return os.environ.get("DEFAULT_TENANT_SLUG", "default")


_lock = threading.Lock()
_cache: dict[str, TenantRuntime] = {}


def get_or_build_runtime(cache_key: str, builder) -> TenantRuntime:
    """Returns the cached TenantRuntime for `cache_key` if it's younger than the TTL,
    otherwise builds a fresh one via `builder()` (called outside the lock, so a concurrent
    cache-miss race can build the same tenant's runtime twice — both are valid, the loser is
    simply garbage collected; acceptable given the TTL keeps this rare)."""
    now = time.monotonic()
    with _lock:
        cached = _cache.get(cache_key)
        if cached is not None and (now - cached.built_at) < _RUNTIME_TTL_SECONDS:
            return cached

    runtime = builder()
    with _lock:
        _cache[cache_key] = runtime
    return runtime


def invalidate(cache_key: str) -> None:
    """Drops one tenant's cached runtime — call after an admin config change so the next
    request rebuilds against the new adapter/LLM/promo config instead of waiting out the TTL."""
    with _lock:
        _cache.pop(cache_key, None)


def clear_all() -> None:
    """Test-only: drops every cached runtime."""
    with _lock:
        _cache.clear()
