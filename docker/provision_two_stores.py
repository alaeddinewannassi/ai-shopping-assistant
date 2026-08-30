#!/usr/bin/env python3
"""Provisions the two demo tenants for the manual dual-PrestaShop test stack
(docker/README-two-stores.md): two Tenant rows pointing at the two live PrestaShop
instances docker-compose.yml brings up, plus ONE admin account with OWNER membership on
BOTH — so you can log into the backoffice once and use the tenant switcher to manage
either store, alongside verifying each store's widget independently.

Must run with the SAME PrestaShop connection details you just set up by hand in each
store's Admin (Webservice key, checkout customer/address/carrier — see
docker/prestashop/README.md, done once per store). Widget keys are FIXED strings, not
randomly generated, matching the ones docker/prestashop/Dockerfile already baked into each
store's footer — this script does not choose them, it just registers the same value PrestaShop is already serving.

Run from backoffice/backend's own venv (has tenancy_db + argon2-cffi installed):
    cd backoffice/backend && source .venv/bin/activate
    DATABASE_URL=postgresql+psycopg://assistant:assistant@localhost:5432/assistant \\
    APP_ENCRYPTION_KEY=<same key backoffice/backend/.env uses> \\
    LLM_API_KEY=<your real Groq key> \\
    python ../../docker/provision_two_stores.py \\
      --admin-email you@example.com --admin-password 'change-me' \\
      --store-one-api-key <webservice key from store one admin> \\
      --store-one-customer-id 1 --store-one-address-id 1 --store-one-carrier-id 1 \\
      --store-two-api-key <webservice key from store two admin> \\
      --store-two-customer-id 1 --store-two-address-id 1 --store-two-carrier-id 1

Idempotent — safe to re-run (e.g. after rotating a webservice key).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

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

STORES = [
    {
        "slug": "demo-store-one",
        "name": "Demo Store One",
        "base_url": "http://prestashop/api",
        "host_header": "localhost:8080",
        "widget_key": "demo-widget-key-store-one",
        "prefix": "store_one",
    },
    {
        "slug": "demo-store-two",
        "name": "Demo Store Two",
        "base_url": "http://prestashop-two/api",
        "host_header": "localhost:8090",
        "widget_key": "demo-widget-key-store-two",
        "prefix": "store_two",
    },
]


def _seed_store(db, args: argparse.Namespace, llm_api_key: str | None, spec: dict) -> None:
    prefix = spec["prefix"]
    api_key = getattr(args, f"{prefix}_api_key")

    tenants = TenantRepository(db)
    tenant = tenants.get_by_slug(spec["slug"])
    if tenant is None:
        tenant = tenants.create(spec["slug"], spec["name"])

    TenantAdapterConfigRepository(db).upsert(
        tenant.id,
        platform="prestashop",
        base_url=spec["base_url"],
        api_key_encrypted=encrypt_secret(api_key),
        lang_id=getattr(args, f"{prefix}_lang_id"),
        host_header=spec["host_header"],
        default_customer_id=getattr(args, f"{prefix}_customer_id"),
        default_address_id=getattr(args, f"{prefix}_address_id"),
        default_carrier_id=getattr(args, f"{prefix}_carrier_id"),
    )

    TenantLLMConfigRepository(db).upsert(
        tenant.id,
        provider="free-tier-hosted" if llm_api_key else "rule-based-stub",
        model=os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile"),
        api_key_encrypted=encrypt_secret(llm_api_key) if llm_api_key else None,
    )

    widget_keys = WidgetKeyRepository(db)
    existing = {k.public_key for k in widget_keys.list_for_tenant(tenant.id)}
    if spec["widget_key"] not in existing:
        widget_keys.issue(tenant.id, spec["widget_key"], [])

    if args.promo_rules_json:
        with open(args.promo_rules_json) as f:
            rules = json.load(f)
        promo_repo = TenantPromoRuleRepository(db)
        for rule in rules:
            promo_repo.upsert_rule(
                tenant.id,
                rule["rule_id"],
                condition=rule["condition"],
                target_code=rule["target_code"],
                priority=rule.get("priority", 0),
                stackable_with=rule.get("stackable_with", []),
            )

    print(f"Provisioned tenant {spec['slug']!r} — widget key {spec['widget_key']!r}, "
          f"PrestaShop at {spec['base_url']!r}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument(
        "--promo-rules-json",
        default=os.path.join(_BACKOFFICE_BACKEND, "..", "..", "chatbot", "backend", "src", "promo", "rules.json"),
        help="Set to '' to skip seeding promo rules.",
    )
    for prefix, label in (("store_one", "store-one"), ("store_two", "store-two")):
        parser.add_argument(f"--{label}-api-key", dest=f"{prefix}_api_key", required=True,
                             help="Webservice key generated in this store's Admin (docker/prestashop/README.md).")
        parser.add_argument(f"--{label}-lang-id", dest=f"{prefix}_lang_id", type=int, default=1)
        parser.add_argument(f"--{label}-customer-id", dest=f"{prefix}_customer_id", required=True)
        parser.add_argument(f"--{label}-address-id", dest=f"{prefix}_address_id", required=True)
        parser.add_argument(f"--{label}-carrier-id", dest=f"{prefix}_carrier_id", required=True)
    args = parser.parse_args(argv)

    engine = get_engine()
    if engine is None:
        print("DATABASE_URL must be set.", file=sys.stderr)
        return 1
    Base.metadata.create_all(engine)

    llm_api_key = os.environ.get("LLM_API_KEY")
    if not llm_api_key:
        print("No LLM_API_KEY set — both tenants will use rule-based-stub (no real LLM).", file=sys.stderr)

    with session_scope() as db:
        for spec in STORES:
            _seed_store(db, args, llm_api_key, spec)

        users = AdminUserRepository(db)
        admin = users.get_by_email(args.admin_email)
        if admin is None:
            admin = users.create(args.admin_email, hash_password(args.admin_password), args.admin_email)
            print(f"Created admin {args.admin_email!r}.")
        else:
            admin.password_hash = hash_password(args.admin_password)
            print(f"Admin {args.admin_email!r} already existed — password reset to the value you passed.")

        memberships = TenantMembershipRepository(db)
        for spec in STORES:
            tenant = TenantRepository(db).get_by_slug(spec["slug"])
            if memberships.get_role(tenant.id, admin.id) is None:
                memberships.add_member(tenant.id, admin.id, AdminRole.OWNER)

    print(f"\nDone. Log into the backoffice as {args.admin_email!r} — the tenant switcher "
          f"will show both Demo Store One and Demo Store Two.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
