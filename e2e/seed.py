#!/usr/bin/env python3
"""Seeds TWO throwaway tenants for the full-journey Playwright E2E test — two different
"shopping websites", each with its own widget key and its own tenant-scoped admin (not a
shared superadmin), so the backoffice-verification tail can prove real cross-tenant
isolation: each admin can only ever see their own store's sessions/analytics.

Reads `DATABASE_URL`/`APP_ENCRYPTION_KEY`/`LLM_API_KEY` from the environment — set by
`playwright.config.ts` so every process in the run (this script, the chatbot backend, the
backoffice backend) shares the same values. Both tenants use `platform="mock"` (no real
store — this suite is about conversational NLU + the confirm-gate + multi-tenant analytics,
not PrestaShop connectivity, which the separate contract test suite already covers) and the
real `free-tier-hosted` LLM provider. Idempotent — safe to re-run.

Invoked with backoffice/backend's own venv (already has `tenancy_db` + `argon2-cffi`
installed) so it can reuse `src.auth.passwords`/`src.auth.widget_keys` rather than
duplicating that logic.

Prints exactly one line, `SEED_RESULT: {...json...}`, that `scripts/seed-db.mjs` parses to
learn each store's tenant id, widget key, and admin credentials.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import uuid

_BACKOFFICE_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backoffice", "backend")
sys.path.insert(0, _BACKOFFICE_BACKEND)

from tenancy_db.base import Base  # noqa: E402
from tenancy_db.crypto import encrypt_secret  # noqa: E402
from tenancy_db.engine import get_engine, session_scope  # noqa: E402
from tenancy_db.models.admin import AdminRole  # noqa: E402
from tenancy_db.repositories import (  # noqa: E402
    AdminUserRepository,
    TenantAdapterConfigRepository,
    TenantLLMConfigRepository,
    TenantMembershipRepository,
    TenantPromoRuleRepository,
    TenantRepository,
    WidgetKeyRepository,
)

from src.auth.passwords import hash_password  # noqa: E402
from src.auth.widget_keys import generate_public_key  # noqa: E402

STORES = [
    {"slug": "e2e-store-one", "name": "E2E Store One", "admin_email": "e2e-owner-one@example.com"},
    {"slug": "e2e-store-two", "name": "E2E Store Two", "admin_email": "e2e-owner-two@example.com"},
]


def _seed_store(db, llm_api_key: str, spec: dict) -> dict:
    tenants = TenantRepository(db)
    tenant = tenants.get_by_slug(spec["slug"])
    if tenant is None:
        tenant = tenants.create(spec["slug"], spec["name"])

    # MockAdapter takes no real connection details — these are placeholders to satisfy the
    # NOT NULL columns; src/tenancy/runtime.py never reads them for platform="mock".
    TenantAdapterConfigRepository(db).upsert(
        tenant.id,
        platform="mock",
        base_url=f"https://{spec['slug']}.example/api",
        api_key_encrypted=encrypt_secret("unused-for-mock-adapter"),
    )
    TenantLLMConfigRepository(db).upsert(
        tenant.id,
        provider="free-tier-hosted",
        model=os.environ.get("LLM_MODEL", "openai/gpt-oss-120b"),
        api_key_encrypted=encrypt_secret(llm_api_key),
    )
    TenantPromoRuleRepository(db).upsert_rule(
        tenant.id, "welcome10", condition="first_order", target_code="WELCOME10", priority=5,
        stackable_with=[],
    )

    widget_keys = WidgetKeyRepository(db)
    existing_keys = widget_keys.list_for_tenant(tenant.id)
    widget_key = existing_keys[0] if existing_keys else widget_keys.issue(tenant.id, generate_public_key(), [])

    # A tenant-scoped OWNER, not a superadmin — a superadmin sees every tenant by design, so
    # it wouldn't actually prove cross-tenant isolation the way a scoped admin does.
    users = AdminUserRepository(db)
    admin_email = spec["admin_email"]
    admin_password = secrets.token_urlsafe(16)
    admin = users.get_by_email(admin_email)
    if admin is None:
        admin = users.create(admin_email, hash_password(admin_password), spec["name"] + " Owner")
    else:
        admin.password_hash = hash_password(admin_password)  # refresh each run to a known password

    memberships = TenantMembershipRepository(db)
    if memberships.get_role(tenant.id, admin.id) is None:
        memberships.add_member(tenant.id, admin.id, AdminRole.OWNER)

    return {
        "slug": spec["slug"],
        "tenant_id": str(tenant.id),
        "widget_public_key": widget_key.public_key,
        "admin_email": admin_email,
        "admin_password": admin_password,
    }


MULTI_ADMIN_EMAIL = "e2e-multi-admin@example.com"


def _seed_multi_tenant_admin(db, stores: list[dict]) -> dict:
    """One admin with VIEWER membership on BOTH seeded tenants — proves the complementary
    "1 backoffice login, multiple stores" story via the tenant switcher, alongside the
    per-store scoped owners above that prove isolation."""
    users = AdminUserRepository(db)
    password = secrets.token_urlsafe(16)
    admin = users.get_by_email(MULTI_ADMIN_EMAIL)
    if admin is None:
        admin = users.create(MULTI_ADMIN_EMAIL, hash_password(password), "Multi-Store Admin")
    else:
        admin.password_hash = hash_password(password)

    memberships = TenantMembershipRepository(db)
    for store in stores:
        tenant_id = uuid.UUID(store["tenant_id"])
        if memberships.get_role(tenant_id, admin.id) is None:
            memberships.add_member(tenant_id, admin.id, AdminRole.ANALYST)

    return {"admin_email": MULTI_ADMIN_EMAIL, "admin_password": password}


def main() -> None:
    llm_api_key = os.environ["LLM_API_KEY"]

    engine = get_engine()
    if engine is None:
        raise SystemExit("DATABASE_URL must be set before running seed.py")
    Base.metadata.create_all(engine)

    with session_scope() as db:
        stores = [_seed_store(db, llm_api_key, spec) for spec in STORES]
        multi_admin = _seed_multi_tenant_admin(db, stores)

    print(f"SEED_RESULT: {json.dumps({'stores': stores, 'multi_admin': multi_admin})}")


if __name__ == "__main__":
    main()
