"""Bootstrap script (T702): migrates today's single-tenant, `.env`-driven chatbot
deployment into a DB-backed tenant, and creates the first superadmin account.

Idempotent — safe to re-run: reuses an existing tenant/admin by slug/email, and always
upserts adapter/LLM config to the current env values (so re-running after rotating
PRESTASHOP_API_KEY, say, re-encrypts and stores the new one).

Real gap, not silently glossed over: promo rules are NOT read from process env (chatbot's
promo/rules.json isn't an env var) — pass --promo-rules-json to migrate them explicitly.
Skip it only if you're certain the legacy deployment had no promo rules configured, or you
plan to re-enter them via the admin API/UI afterward — otherwise a bootstrapped tenant's
promo suggestions silently disappear the moment this tenant row exists (chatbot/backend
switches from its env-based fallback to this DB-backed tenant as soon as the slug matches
DEFAULT_TENANT_SLUG, per src/tenancy/config.py's resolution order in chatbot/backend).

Usage:
    python -m scripts.bootstrap \
        --superadmin-email root@example.com --superadmin-password 'change-me' \
        --promo-rules-json ../../chatbot/backend/src/promo/rules.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from tenancy_db.base import Base
from tenancy_db.crypto import encrypt_secret
from tenancy_db.engine import get_engine, session_scope
from tenancy_db.repositories import (
    AdminUserRepository,
    TenantAdapterConfigRepository,
    TenantLLMConfigRepository,
    TenantPromoRuleRepository,
    TenantRepository,
)

from src.auth.passwords import hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant-slug", default=os.environ.get("DEFAULT_TENANT_SLUG", "default"))
    parser.add_argument("--tenant-name", default=None)
    parser.add_argument("--superadmin-email", required=True)
    parser.add_argument("--superadmin-password", required=True)
    parser.add_argument(
        "--promo-rules-json",
        default=None,
        help="Path to a rules.json file (chatbot/backend/src/promo/rules.json's format).",
    )
    args = parser.parse_args(argv)

    engine = get_engine()
    if engine is None:
        print("DATABASE_URL is not set — nothing to bootstrap into.", file=sys.stderr)
        return 1
    Base.metadata.create_all(engine)

    with session_scope() as db:
        tenants = TenantRepository(db)
        tenant = tenants.get_by_slug(args.tenant_slug)
        created_tenant = tenant is None
        if tenant is None:
            tenant = tenants.create(args.tenant_slug, args.tenant_name or args.tenant_slug)

        has_prestashop = bool(os.environ.get("PRESTASHOP_BASE_URL")) and bool(
            os.environ.get("PRESTASHOP_API_KEY")
        )
        if has_prestashop:
            TenantAdapterConfigRepository(db).upsert(
                tenant.id,
                platform="prestashop",
                base_url=os.environ["PRESTASHOP_BASE_URL"],
                api_key_encrypted=encrypt_secret(os.environ["PRESTASHOP_API_KEY"]),
                lang_id=int(os.environ.get("PRESTASHOP_LANG_ID", "1")),
                host_header=os.environ.get("PRESTASHOP_HOST_HEADER"),
                default_customer_id=os.environ.get("PRESTASHOP_DEFAULT_CUSTOMER_ID"),
                default_address_id=os.environ.get("PRESTASHOP_DEFAULT_ADDRESS_ID"),
                default_carrier_id=os.environ.get("PRESTASHOP_DEFAULT_CARRIER_ID"),
                default_currency_id=os.environ.get("PRESTASHOP_DEFAULT_CURRENCY_ID"),
                default_order_state_id=os.environ.get("PRESTASHOP_DEFAULT_ORDER_STATE_ID"),
                payment_module=os.environ.get("PRESTASHOP_PAYMENT_MODULE"),
                payment_label=os.environ.get("PRESTASHOP_PAYMENT_LABEL"),
            )
            print(f"Migrated adapter config for tenant {tenant.slug!r} (prestashop).")
        else:
            print(
                f"No PRESTASHOP_BASE_URL/PRESTASHOP_API_KEY in env — tenant {tenant.slug!r} "
                f"will fall back to MockAdapter (chatbot's legacy_env_tenant_config default) "
                f"until adapter config is set via the admin API."
            )

        llm_provider = os.environ.get("LLM_PROVIDER", "rule-based-stub")
        llm_api_key = os.environ.get("LLM_API_KEY")
        TenantLLMConfigRepository(db).upsert(
            tenant.id,
            provider=llm_provider,
            model=os.environ.get("LLM_MODEL"),
            api_key_encrypted=encrypt_secret(llm_api_key) if llm_api_key else None,
        )
        print(f"Migrated LLM config for tenant {tenant.slug!r} (provider={llm_provider!r}).")

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
            print(f"Migrated {len(rules)} promo rule(s) for tenant {tenant.slug!r}.")
        else:
            print(
                f"WARNING: no --promo-rules-json given — tenant {tenant.slug!r} has NO promo "
                f"rules. If the legacy deployment had promo/rules.json configured, its "
                f"suggestions silently stop the moment this tenant row exists."
            )

        users = AdminUserRepository(db)
        existing_admin = users.get_by_email(args.superadmin_email)
        if existing_admin is None:
            users.create(
                args.superadmin_email,
                hash_password(args.superadmin_password),
                args.superadmin_email,
                is_superadmin=True,
            )
            print(f"Created superadmin {args.superadmin_email!r}.")
        else:
            print(f"Superadmin {args.superadmin_email!r} already exists — left unchanged.")

    verb = "Created" if created_tenant else "Reused existing"
    print(f"\n{verb} tenant {args.tenant_slug!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
