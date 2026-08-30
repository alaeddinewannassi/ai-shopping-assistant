"""Per-tenant runtime configuration, loaded from the tenancy database (T201).

`TenantConfig` is a plain, adapter-agnostic snapshot of one tenant's settings — secrets
already decrypted — built by either `load_tenant_config()` (reads tenant_adapter_config /
tenant_llm_config / tenant_promo_rule) or `legacy_env_tenant_config()` (replicates today's
process-env-driven single-tenant behavior byte for byte, see src/api/chat.py's
`_build_adapter`/`_build_llm_client`). `src/tenancy/runtime.py` consumes either the same way
— the rest of the app never needs to know which path built a given tenant's config.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from tenancy_db.crypto import decrypt_secret
from tenancy_db.models.tenant import TenantStatus
from tenancy_db.repositories import (
    TenantAdapterConfigRepository,
    TenantLLMConfigRepository,
    TenantPromoRuleRepository,
    TenantRepository,
)

from src.promo.strategy import PromoStrategyRule, load_rules

# Stable namespace so the legacy/default tenant gets the same synthesized UUID across
# restarts (needed as a cache key in src/tenancy/runtime.py) without requiring a DB row.
_LEGACY_TENANT_NAMESPACE = uuid.UUID("6f6d6e61-6c74-6e61-6e74-000000000000")


class TenantNotFoundError(Exception):
    """No Tenant row matches the given id/slug."""


class TenantSuspendedError(Exception):
    """The Tenant row exists but its status is not ACTIVE."""


@dataclass
class TenantConfig:
    tenant_id: uuid.UUID
    slug: str
    name: str

    adapter_platform: str = "mock"
    adapter_base_url: str | None = None
    # repr=False (T705 security review): a plaintext, decrypted secret must never appear in
    # a traceback, `logger.info(config)`, or any other place Python's default dataclass
    # __repr__ would otherwise print it.
    adapter_api_key: str | None = field(default=None, repr=False)
    adapter_lang_id: int = 1
    adapter_host_header: str | None = None
    default_customer_id: str | None = None
    default_address_id: str | None = None
    default_carrier_id: str | None = None
    default_currency_id: str | None = None
    default_order_state_id: str | None = None
    payment_module: str | None = None
    payment_label: str | None = None

    llm_provider: str = "rule-based-stub"
    llm_model: str | None = None
    llm_api_key: str | None = field(default=None, repr=False)  # decrypted, never in repr

    promo_rules: list[PromoStrategyRule] = field(default_factory=list)


def load_tenant_config(session: Session, tenant_ref: str, *, by_slug: bool) -> TenantConfig:
    """Loads one tenant's full runtime config from the database.

    Raises TenantNotFoundError / TenantSuspendedError rather than returning a partial or
    silently-defaulted config — an unknown or suspended tenant must never fall through to
    look like a valid, empty-config tenant.
    """
    tenants = TenantRepository(session)
    tenant = tenants.get_by_slug(tenant_ref) if by_slug else tenants.get_by_id(uuid.UUID(str(tenant_ref)))
    if tenant is None:
        raise TenantNotFoundError(f"No tenant found for {tenant_ref!r}")
    if tenant.status != TenantStatus.ACTIVE:
        raise TenantSuspendedError(f"Tenant {tenant.slug!r} is not active (status={tenant.status.value})")

    config = TenantConfig(tenant_id=tenant.id, slug=tenant.slug, name=tenant.name)

    adapter_row = TenantAdapterConfigRepository(session).get_for_tenant(tenant.id)
    if adapter_row is not None and adapter_row.is_active:
        config.adapter_platform = adapter_row.platform
        config.adapter_base_url = adapter_row.base_url
        config.adapter_api_key = (
            decrypt_secret(adapter_row.api_key_encrypted) if adapter_row.api_key_encrypted else None
        )
        config.adapter_lang_id = adapter_row.lang_id
        config.adapter_host_header = adapter_row.host_header
        config.default_customer_id = adapter_row.default_customer_id
        config.default_address_id = adapter_row.default_address_id
        config.default_carrier_id = adapter_row.default_carrier_id
        config.default_currency_id = adapter_row.default_currency_id
        config.default_order_state_id = adapter_row.default_order_state_id
        config.payment_module = adapter_row.payment_module
        config.payment_label = adapter_row.payment_label

    llm_row = TenantLLMConfigRepository(session).get_for_tenant(tenant.id)
    if llm_row is not None and llm_row.is_active:
        config.llm_provider = llm_row.provider
        config.llm_model = llm_row.model
        config.llm_api_key = decrypt_secret(llm_row.api_key_encrypted) if llm_row.api_key_encrypted else None

    config.promo_rules = [
        PromoStrategyRule(
            rule_id=row.rule_id,
            condition=row.condition,
            target_code=row.target_code,
            priority=row.priority,
            stackable_with=list(row.stackable_with),
        )
        for row in TenantPromoRuleRepository(session).list_active_for_tenant(tenant.id)
    ]
    return config


def legacy_env_tenant_config(slug: str) -> TenantConfig:
    """Reconstructs today's (pre-002) single-tenant config straight from process env vars —
    used when DATABASE_URL is unset, or when it's set but no tenant row exists yet for
    `slug` (nobody has run the T702 bootstrap). Byte-for-byte the same adapter/LLM selection
    logic as the singletons src/api/chat.py built at import time, so every existing
    deployment and the whole pre-002 test suite keep working unchanged (plan.md D2)."""
    has_prestashop = bool(os.environ.get("PRESTASHOP_BASE_URL")) and bool(
        os.environ.get("PRESTASHOP_API_KEY")
    )
    try:
        promo_rules = load_rules()
    except FileNotFoundError:
        promo_rules = []

    return TenantConfig(
        tenant_id=uuid.uuid5(_LEGACY_TENANT_NAMESPACE, slug),
        slug=slug,
        name=slug,
        adapter_platform="prestashop" if has_prestashop else "mock",
        adapter_base_url=os.environ.get("PRESTASHOP_BASE_URL"),
        adapter_api_key=os.environ.get("PRESTASHOP_API_KEY"),
        adapter_lang_id=int(os.environ.get("PRESTASHOP_LANG_ID", "1")),
        adapter_host_header=os.environ.get("PRESTASHOP_HOST_HEADER"),
        default_customer_id=os.environ.get("PRESTASHOP_DEFAULT_CUSTOMER_ID"),
        default_address_id=os.environ.get("PRESTASHOP_DEFAULT_ADDRESS_ID"),
        default_carrier_id=os.environ.get("PRESTASHOP_DEFAULT_CARRIER_ID"),
        default_currency_id=os.environ.get("PRESTASHOP_DEFAULT_CURRENCY_ID"),
        default_order_state_id=os.environ.get("PRESTASHOP_DEFAULT_ORDER_STATE_ID"),
        payment_module=os.environ.get("PRESTASHOP_PAYMENT_MODULE"),
        payment_label=os.environ.get("PRESTASHOP_PAYMENT_LABEL"),
        llm_provider=os.environ.get("LLM_PROVIDER", "rule-based-stub"),
        llm_model=os.environ.get("LLM_MODEL"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        promo_rules=promo_rules,
    )
