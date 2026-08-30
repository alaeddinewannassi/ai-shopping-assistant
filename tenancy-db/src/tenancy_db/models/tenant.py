"""Tenant + per-tenant configuration tables (T104).

One `Tenant` row per merchant deployment. Everything else in this module scopes to a
tenant_id and is read by `src/tenancy/runtime.py` (T201-T203) to build that tenant's
adapter/LLM client/promo rules — replacing the module-level singletons `src/api/chat.py`
built at import time before this feature.
"""

from __future__ import annotations

import enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tenancy_db.base import Base, JSONType, created_at_column, updated_at_column, uuid_pk


class TenantStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[object] = uuid_pk()
    slug: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        sa.Enum(TenantStatus, native_enum=False), nullable=False, default=TenantStatus.ACTIVE
    )
    plan: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="standard")
    timezone: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="UTC")
    settings: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at = created_at_column()
    updated_at = updated_at_column()

    adapter_config: Mapped[TenantAdapterConfig | None] = relationship(
        back_populates="tenant", uselist=False, cascade="all, delete-orphan"
    )
    llm_config: Mapped[TenantLLMConfig | None] = relationship(
        back_populates="tenant", uselist=False, cascade="all, delete-orphan"
    )
    promo_rules: Mapped[list[TenantPromoRule]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    widget_keys: Mapped[list[WidgetKey]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class TenantAdapterConfig(Base):
    """CommerceAdapter selection + credentials for one tenant (mirrors backend/.env.example's
    PRESTASHOP_* variables, now per-tenant instead of process-wide). `api_key_encrypted` is
    written/read only via src/db/crypto.py — never stored or returned in plaintext."""

    __tablename__ = "tenant_adapter_config"

    id: Mapped[object] = uuid_pk()
    tenant_id: Mapped[object] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    platform: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="prestashop")
    base_url: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    host_header: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    lang_id: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    api_key_encrypted: Mapped[str] = mapped_column(sa.Text, nullable=False)

    default_customer_id: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    default_address_id: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    default_carrier_id: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    default_currency_id: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    default_order_state_id: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    payment_module: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    payment_label: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)

    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_at = created_at_column()
    updated_at = updated_at_column()

    tenant: Mapped[Tenant] = relationship(back_populates="adapter_config")


class TenantLLMConfig(Base):
    __tablename__ = "tenant_llm_config"

    id: Mapped[object] = uuid_pk()
    tenant_id: Mapped[object] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="rule-based-stub")
    model: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    monthly_token_budget: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    budget_action: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="warn")

    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_at = created_at_column()
    updated_at = updated_at_column()

    tenant: Mapped[Tenant] = relationship(back_populates="llm_config")


class TenantPromoRule(Base):
    """Per-tenant version of src/promo/strategy.py's PromoStrategyRule (data-model.md's
    PromoStrategy entity) — replaces the single global promo/rules.json."""

    __tablename__ = "tenant_promo_rule"

    id: Mapped[object] = uuid_pk()
    tenant_id: Mapped[object] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    condition: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    target_code: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    priority: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    stackable_with: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_at = created_at_column()
    updated_at = updated_at_column()

    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "rule_id", name="uq_tenant_promo_rule_tenant_rule"),
    )

    tenant: Mapped[Tenant] = relationship(back_populates="promo_rules")


class WidgetKey(Base):
    """A public, origin-restricted key the embeddable widget sends as `X-Assistant-Key`
    (plan.md D2) — resolved to a tenant by src/tenancy/resolver.py. Public by design: it
    ships in browser JS, so abuse control is the origin allowlist + rate limiting, not
    secrecy."""

    __tablename__ = "widget_key"

    id: Mapped[object] = uuid_pk()
    tenant_id: Mapped[object] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    public_key: Mapped[str] = mapped_column(sa.String(80), nullable=False, unique=True, index=True)
    allowed_origins: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    last_used_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at = created_at_column()

    tenant: Mapped[Tenant] = relationship(back_populates="widget_keys")
