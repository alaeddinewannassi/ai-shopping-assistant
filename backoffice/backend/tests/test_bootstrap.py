"""Bootstrap script (T702) — the legacy-env-to-DB migration path."""

from __future__ import annotations

import json

import sqlalchemy as sa
from tenancy_db.engine import session_scope
from tenancy_db.models.tenant import TenantAdapterConfig, TenantLLMConfig, TenantPromoRule

from scripts.bootstrap import main


def test_bootstrap_migrates_adapter_llm_and_promo_config(monkeypatch, tmp_path, seeded) -> None:
    monkeypatch.setenv("PRESTASHOP_BASE_URL", "https://legacy-store.example/api")
    monkeypatch.setenv("PRESTASHOP_API_KEY", "legacy-webservice-key")
    monkeypatch.setenv("PRESTASHOP_DEFAULT_CUSTOMER_ID", "42")
    monkeypatch.setenv("LLM_PROVIDER", "rule-based-stub")

    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            [
                {"rule_id": "welcome10", "condition": "first_order", "target_code": "WELCOME10", "priority": 5},
                {"rule_id": "bigcart15", "condition": "subtotal>=100", "target_code": "BIGCART15", "priority": 10},
            ]
        )
    )

    exit_code = main(
        [
            "--tenant-slug",
            "migrated-store",
            "--superadmin-email",
            "newroot@example.com",
            "--superadmin-password",
            "a-strong-password",
            "--promo-rules-json",
            str(rules_path),
        ]
    )
    assert exit_code == 0

    from tenancy_db.repositories import TenantRepository

    with session_scope() as db:
        tenant = TenantRepository(db).get_by_slug("migrated-store")
        assert tenant is not None

        adapter = db.scalars(
            sa.select(TenantAdapterConfig).where(TenantAdapterConfig.tenant_id == tenant.id)
        ).first()
        assert adapter.base_url == "https://legacy-store.example/api"
        assert adapter.api_key_encrypted != "legacy-webservice-key"
        assert adapter.default_customer_id == "42"

        llm = db.scalars(sa.select(TenantLLMConfig).where(TenantLLMConfig.tenant_id == tenant.id)).first()
        assert llm.provider == "rule-based-stub"

        rules = db.scalars(sa.select(TenantPromoRule).where(TenantPromoRule.tenant_id == tenant.id)).all()
        assert {r.rule_id for r in rules} == {"welcome10", "bigcart15"}

    from tenancy_db.repositories import AdminUserRepository

    with session_scope() as db:
        admin = AdminUserRepository(db).get_by_email("newroot@example.com")
        assert admin is not None
        assert admin.is_superadmin is True


def test_bootstrap_is_idempotent(monkeypatch, seeded) -> None:
    monkeypatch.delenv("PRESTASHOP_BASE_URL", raising=False)
    monkeypatch.delenv("PRESTASHOP_API_KEY", raising=False)

    args = [
        "--tenant-slug",
        "idempotent-store",
        "--superadmin-email",
        "idempotent-root@example.com",
        "--superadmin-password",
        "a-strong-password",
    ]
    assert main(args) == 0
    assert main(args) == 0  # must not raise/duplicate on a second run

    from tenancy_db.repositories import AdminUserRepository, TenantRepository

    with session_scope() as db:
        assert TenantRepository(db).get_by_slug("idempotent-store") is not None
        assert AdminUserRepository(db).get_by_email("idempotent-root@example.com") is not None


def test_bootstrap_without_database_url_fails_cleanly(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from tenancy_db.engine import reset_engine

    reset_engine()
    exit_code = main(["--superadmin-email", "x@example.com", "--superadmin-password", "y"])
    assert exit_code == 1
