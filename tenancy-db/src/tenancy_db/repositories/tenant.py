"""Tenant + per-tenant config repositories (T106).

Every method that touches a tenant-scoped table takes `tenant_id` as its first argument and
filters on it explicitly — this is the primary isolation guarantee (T107 tests it directly);
Postgres RLS (migrations/versions/4024383a2d62_tenancy_baseline.py) is a defense-in-depth
backstop, not a substitute for this filter. `session_scope()`'s tenant-context wrapper
(`tenancy_db.tenant_context.scoped_to_tenant`) is applied here too so both layers stay in sync.

The one deliberate exception is `WidgetKeyRepository.get_by_public_key`: resolving a widget
key IS how a request's tenant gets determined in the first place (plan.md D2), so that one
lookup is necessarily un-scoped — everything after it operates within the tenant it returns.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from tenancy_db.models.tenant import (
    Tenant,
    TenantAdapterConfig,
    TenantLLMConfig,
    TenantPromoRule,
    TenantStatus,
    WidgetKey,
)
from tenancy_db.tenant_context import scoped_to_tenant


class TenantRepository:
    """The one repository not scoped by tenant_id — it's what enumerates/creates tenants."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, slug: str, name: str, *, plan: str = "standard", timezone: str = "UTC") -> Tenant:
        tenant = Tenant(slug=slug, name=name, plan=plan, timezone=timezone)
        self._session.add(tenant)
        self._session.flush()
        return tenant

    def get_by_id(self, tenant_id: uuid.UUID) -> Tenant | None:
        return self._session.get(Tenant, tenant_id)

    def get_by_slug(self, slug: str) -> Tenant | None:
        stmt = sa.select(Tenant).where(Tenant.slug == slug)
        return self._session.scalars(stmt).first()

    def list_active(self) -> list[Tenant]:
        stmt = sa.select(Tenant).where(Tenant.status == TenantStatus.ACTIVE).order_by(Tenant.name)
        return list(self._session.scalars(stmt).all())


class TenantAdapterConfigRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_tenant(self, tenant_id: uuid.UUID) -> TenantAdapterConfig | None:
        with scoped_to_tenant(self._session, tenant_id):
            stmt = sa.select(TenantAdapterConfig).where(TenantAdapterConfig.tenant_id == tenant_id)
            return self._session.scalars(stmt).first()

    def upsert(self, tenant_id: uuid.UUID, **fields: object) -> TenantAdapterConfig:
        with scoped_to_tenant(self._session, tenant_id):
            existing = self.get_for_tenant(tenant_id)
            if existing is not None:
                for key, value in fields.items():
                    setattr(existing, key, value)
                self._session.flush()
                return existing
            config = TenantAdapterConfig(tenant_id=tenant_id, **fields)
            self._session.add(config)
            self._session.flush()
            return config


class TenantLLMConfigRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_tenant(self, tenant_id: uuid.UUID) -> TenantLLMConfig | None:
        with scoped_to_tenant(self._session, tenant_id):
            stmt = sa.select(TenantLLMConfig).where(TenantLLMConfig.tenant_id == tenant_id)
            return self._session.scalars(stmt).first()

    def upsert(self, tenant_id: uuid.UUID, **fields: object) -> TenantLLMConfig:
        with scoped_to_tenant(self._session, tenant_id):
            existing = self.get_for_tenant(tenant_id)
            if existing is not None:
                for key, value in fields.items():
                    setattr(existing, key, value)
                self._session.flush()
                return existing
            config = TenantLLMConfig(tenant_id=tenant_id, **fields)
            self._session.add(config)
            self._session.flush()
            return config


class TenantPromoRuleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_active_for_tenant(self, tenant_id: uuid.UUID) -> list[TenantPromoRule]:
        with scoped_to_tenant(self._session, tenant_id):
            stmt = (
                sa.select(TenantPromoRule)
                .where(TenantPromoRule.tenant_id == tenant_id, TenantPromoRule.is_active.is_(True))
                .order_by(TenantPromoRule.priority.desc())
            )
            return list(self._session.scalars(stmt).all())

    def upsert_rule(self, tenant_id: uuid.UUID, rule_id: str, **fields: object) -> TenantPromoRule:
        with scoped_to_tenant(self._session, tenant_id):
            stmt = sa.select(TenantPromoRule).where(
                TenantPromoRule.tenant_id == tenant_id, TenantPromoRule.rule_id == rule_id
            )
            existing = self._session.scalars(stmt).first()
            if existing is not None:
                for key, value in fields.items():
                    setattr(existing, key, value)
                self._session.flush()
                return existing
            rule = TenantPromoRule(tenant_id=tenant_id, rule_id=rule_id, **fields)
            self._session.add(rule)
            self._session.flush()
            return rule


class WidgetKeyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def issue(self, tenant_id: uuid.UUID, public_key: str, allowed_origins: list[str]) -> WidgetKey:
        with scoped_to_tenant(self._session, tenant_id):
            key = WidgetKey(tenant_id=tenant_id, public_key=public_key, allowed_origins=allowed_origins)
            self._session.add(key)
            self._session.flush()
            return key

    def list_for_tenant(self, tenant_id: uuid.UUID) -> list[WidgetKey]:
        with scoped_to_tenant(self._session, tenant_id):
            stmt = sa.select(WidgetKey).where(WidgetKey.tenant_id == tenant_id)
            return list(self._session.scalars(stmt).all())

    def revoke(self, tenant_id: uuid.UUID, key_id: uuid.UUID) -> None:
        with scoped_to_tenant(self._session, tenant_id):
            stmt = sa.select(WidgetKey).where(
                WidgetKey.tenant_id == tenant_id, WidgetKey.id == key_id
            )
            key = self._session.scalars(stmt).first()
            if key is not None:
                key.is_active = False
                from tenancy_db.base import utcnow

                key.revoked_at = utcnow()
                self._session.flush()

    def get_by_public_key(self, public_key: str) -> WidgetKey | None:
        """Deliberately un-scoped — see module docstring. Only ever called to *discover*
        which tenant a request belongs to (src/tenancy/resolver.py, T202)."""
        stmt = sa.select(WidgetKey).where(
            WidgetKey.public_key == public_key, WidgetKey.is_active.is_(True)
        )
        return self._session.scalars(stmt).first()
