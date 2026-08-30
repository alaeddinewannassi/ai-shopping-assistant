"""Tenant CRUD, adapter/LLM config, widget keys, promo rules (T507).

No "Test connection" endpoint — an open design gap, not a silent omission: checking real
connectivity means calling the configured store, and the CommerceAdapter code lives only in
chatbot/backend, which this service deliberately never imports (D6 — no code sharing between
the two backends beyond tenancy-db). Needs either a shared adapter package or an internal
health-check proxy; see specs/002-backoffice-analytics/plan.md.

Every mutating endpoint here writes an admin_audit row (T509).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from tenancy_db.crypto import encrypt_secret
from tenancy_db.models.tenant import TenantStatus
from tenancy_db.repositories import (
    TenantAdapterConfigRepository,
    TenantLLMConfigRepository,
    TenantPromoRuleRepository,
    TenantRepository,
    WidgetKeyRepository,
)

from src.auth import audit as admin_audit
from src.auth.dependencies import (
    ANY_MEMBER,
    MANAGE_TENANT,
    get_db,
    require_superadmin,
    require_tenant_role,
)
from src.auth.widget_keys import generate_public_key

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _masked_or_none(is_set: bool) -> str | None:
    """Never decrypts to show a real partial value — that would mean handling the
    plaintext secret at read time purely for display, which isn't worth the added
    secret-handling surface. Just tells the operator whether a credential is configured."""
    return "••••••••" if is_set else None


# -- Tenant CRUD ----------------------------------------------------------------------- #


class TenantOut(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    plan: str


class TenantCreate(BaseModel):
    slug: str
    name: str
    plan: str = "standard"


class TenantUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    plan: str | None = None


def _to_tenant_out(t) -> TenantOut:
    return TenantOut(id=str(t.id), slug=t.slug, name=t.name, status=t.status.value, plan=t.plan)


@router.get("", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db), _admin=Depends(require_superadmin)) -> list[TenantOut]:
    return [_to_tenant_out(t) for t in TenantRepository(db).list_active()]


@router.post("", response_model=TenantOut, status_code=201)
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    admin_user=Depends(require_superadmin),
) -> TenantOut:
    tenants = TenantRepository(db)
    if tenants.get_by_slug(payload.slug) is not None:
        raise HTTPException(status_code=409, detail="Slug already taken")
    tenant = tenants.create(payload.slug, payload.name, plan=payload.plan)
    admin_audit.record(
        db, admin_user_id=admin_user.id, tenant_id=tenant.id, action="tenant.create", target=tenant.slug
    )
    return _to_tenant_out(tenant)


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin=Depends(require_tenant_role(*ANY_MEMBER)),
) -> TenantOut:
    tenant = TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown tenant")
    return _to_tenant_out(tenant)


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdate,
    db: Session = Depends(get_db),
    admin_user=Depends(require_tenant_role(*MANAGE_TENANT)),
) -> TenantOut:
    tenant = TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Unknown tenant")

    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes:
        try:
            tenant.status = TenantStatus(changes.pop("status"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid status") from exc
    for key, value in changes.items():
        setattr(tenant, key, value)

    admin_audit.record(
        db, admin_user_id=admin_user.id, tenant_id=tenant.id, action="tenant.update",
        target=tenant.slug, details=payload.model_dump(exclude_unset=True),
    )
    return _to_tenant_out(tenant)


# -- Adapter config ---------------------------------------------------------------------- #


class AdapterConfigWrite(BaseModel):
    platform: str
    base_url: str
    api_key: str
    host_header: str | None = None
    lang_id: int = 1
    default_customer_id: str | None = None
    default_address_id: str | None = None
    default_carrier_id: str | None = None
    default_currency_id: str | None = None
    default_order_state_id: str | None = None
    payment_module: str | None = None
    payment_label: str | None = None


class AdapterConfigOut(BaseModel):
    platform: str
    base_url: str
    api_key: str | None
    host_header: str | None
    lang_id: int
    default_customer_id: str | None
    default_address_id: str | None
    default_carrier_id: str | None
    default_currency_id: str | None
    default_order_state_id: str | None
    payment_module: str | None
    payment_label: str | None
    is_active: bool


def _to_adapter_config_out(c) -> AdapterConfigOut:
    return AdapterConfigOut(
        platform=c.platform, base_url=c.base_url, api_key=_masked_or_none(bool(c.api_key_encrypted)),
        host_header=c.host_header, lang_id=c.lang_id, default_customer_id=c.default_customer_id,
        default_address_id=c.default_address_id, default_carrier_id=c.default_carrier_id,
        default_currency_id=c.default_currency_id, default_order_state_id=c.default_order_state_id,
        payment_module=c.payment_module, payment_label=c.payment_label, is_active=c.is_active,
    )


@router.get("/{tenant_id}/adapter-config", response_model=AdapterConfigOut)
def get_adapter_config(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin=Depends(require_tenant_role(*MANAGE_TENANT)),
) -> AdapterConfigOut:
    config = TenantAdapterConfigRepository(db).get_for_tenant(tenant_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Not configured yet")
    return _to_adapter_config_out(config)


@router.put("/{tenant_id}/adapter-config", response_model=AdapterConfigOut)
def upsert_adapter_config(
    tenant_id: uuid.UUID,
    payload: AdapterConfigWrite,
    db: Session = Depends(get_db),
    admin_user=Depends(require_tenant_role(*MANAGE_TENANT)),
) -> AdapterConfigOut:
    fields = payload.model_dump(exclude={"api_key"})
    fields["api_key_encrypted"] = encrypt_secret(payload.api_key)
    config = TenantAdapterConfigRepository(db).upsert(tenant_id, **fields)
    admin_audit.record(
        db, admin_user_id=admin_user.id, tenant_id=tenant_id, action="adapter_config.upsert",
        details={"platform": payload.platform, "base_url": payload.base_url},
    )
    return _to_adapter_config_out(config)


# -- LLM config -------------------------------------------------------------------------- #


class LlmConfigWrite(BaseModel):
    provider: str
    model: str | None = None
    api_key: str | None = None
    monthly_token_budget: int | None = None
    budget_action: str = "warn"


class LlmConfigOut(BaseModel):
    provider: str
    model: str | None
    api_key: str | None
    monthly_token_budget: int | None
    budget_action: str
    is_active: bool


def _to_llm_config_out(c) -> LlmConfigOut:
    return LlmConfigOut(
        provider=c.provider, model=c.model, api_key=_masked_or_none(bool(c.api_key_encrypted)),
        monthly_token_budget=c.monthly_token_budget, budget_action=c.budget_action, is_active=c.is_active,
    )


@router.get("/{tenant_id}/llm-config", response_model=LlmConfigOut)
def get_llm_config(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin=Depends(require_tenant_role(*MANAGE_TENANT)),
) -> LlmConfigOut:
    config = TenantLLMConfigRepository(db).get_for_tenant(tenant_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Not configured yet")
    return _to_llm_config_out(config)


@router.put("/{tenant_id}/llm-config", response_model=LlmConfigOut)
def upsert_llm_config(
    tenant_id: uuid.UUID,
    payload: LlmConfigWrite,
    db: Session = Depends(get_db),
    admin_user=Depends(require_tenant_role(*MANAGE_TENANT)),
) -> LlmConfigOut:
    fields = payload.model_dump(exclude={"api_key"})
    fields["api_key_encrypted"] = encrypt_secret(payload.api_key) if payload.api_key else None
    config = TenantLLMConfigRepository(db).upsert(tenant_id, **fields)
    admin_audit.record(
        db, admin_user_id=admin_user.id, tenant_id=tenant_id, action="llm_config.upsert",
        details={"provider": payload.provider, "model": payload.model},
    )
    return _to_llm_config_out(config)


# -- Widget keys ------------------------------------------------------------------------- #


class WidgetKeyOut(BaseModel):
    id: str
    public_key: str
    allowed_origins: list[str]
    is_active: bool
    last_used_at: str | None


class WidgetKeyCreate(BaseModel):
    allowed_origins: list[str] = []


def _to_widget_key_out(k) -> WidgetKeyOut:
    return WidgetKeyOut(
        id=str(k.id), public_key=k.public_key, allowed_origins=list(k.allowed_origins),
        is_active=k.is_active, last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
    )


@router.get("/{tenant_id}/widget-keys", response_model=list[WidgetKeyOut])
def list_widget_keys(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin=Depends(require_tenant_role(*MANAGE_TENANT)),
) -> list[WidgetKeyOut]:
    return [_to_widget_key_out(k) for k in WidgetKeyRepository(db).list_for_tenant(tenant_id)]


@router.post("/{tenant_id}/widget-keys", response_model=WidgetKeyOut, status_code=201)
def issue_widget_key(
    tenant_id: uuid.UUID,
    payload: WidgetKeyCreate,
    db: Session = Depends(get_db),
    admin_user=Depends(require_tenant_role(*MANAGE_TENANT)),
) -> WidgetKeyOut:
    public_key = generate_public_key()
    key = WidgetKeyRepository(db).issue(tenant_id, public_key, payload.allowed_origins)
    admin_audit.record(
        db, admin_user_id=admin_user.id, tenant_id=tenant_id, action="widget_key.issue",
        target=public_key,
    )
    return _to_widget_key_out(key)


@router.delete("/{tenant_id}/widget-keys/{key_id}", status_code=204)
def revoke_widget_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    admin_user=Depends(require_tenant_role(*MANAGE_TENANT)),
) -> None:
    WidgetKeyRepository(db).revoke(tenant_id, key_id)
    admin_audit.record(
        db, admin_user_id=admin_user.id, tenant_id=tenant_id, action="widget_key.revoke",
        target=str(key_id),
    )
    response.status_code = 204


# -- Promo rules ------------------------------------------------------------------------- #


class PromoRuleWrite(BaseModel):
    condition: str
    target_code: str
    priority: int = 0
    stackable_with: list[str] = []
    is_active: bool = True


class PromoRuleOut(BaseModel):
    rule_id: str
    condition: str
    target_code: str
    priority: int
    stackable_with: list[str]
    is_active: bool


def _to_promo_rule_out(r) -> PromoRuleOut:
    return PromoRuleOut(
        rule_id=r.rule_id, condition=r.condition, target_code=r.target_code,
        priority=r.priority, stackable_with=list(r.stackable_with), is_active=r.is_active,
    )


@router.get("/{tenant_id}/promo-rules", response_model=list[PromoRuleOut])
def list_promo_rules(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin=Depends(require_tenant_role(*ANY_MEMBER)),
) -> list[PromoRuleOut]:
    return [_to_promo_rule_out(r) for r in TenantPromoRuleRepository(db).list_active_for_tenant(tenant_id)]


@router.put("/{tenant_id}/promo-rules/{rule_id}", response_model=PromoRuleOut)
def upsert_promo_rule(
    tenant_id: uuid.UUID,
    rule_id: str,
    payload: PromoRuleWrite,
    db: Session = Depends(get_db),
    admin_user=Depends(require_tenant_role(*MANAGE_TENANT)),
) -> PromoRuleOut:
    rule = TenantPromoRuleRepository(db).upsert_rule(tenant_id, rule_id, **payload.model_dump())
    admin_audit.record(
        db, admin_user_id=admin_user.id, tenant_id=tenant_id, action="promo_rule.upsert", target=rule_id,
        details=payload.model_dump(),
    )
    return _to_promo_rule_out(rule)
