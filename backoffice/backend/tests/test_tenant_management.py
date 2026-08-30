"""Tenant CRUD, adapter/LLM config, widget keys, promo rules, and the admin_audit trail
they must each leave behind (T507/T509)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from tenancy_db.engine import session_scope
from tenancy_db.models.admin import AdminAudit

from tests.conftest import login_as


def _audit_actions(tenant_id) -> list[str]:
    with session_scope() as db:
        stmt = sa.select(AdminAudit.action).where(AdminAudit.tenant_id == tenant_id)
        return list(db.scalars(stmt).all())


def test_superadmin_can_create_and_list_tenants(client, seeded) -> None:
    login_as(client, "root@example.com")
    resp = client.post("/tenants", json={"slug": "store-new", "name": "Store New"})
    assert resp.status_code == 201
    tenant_id = resp.json()["id"]
    assert "tenant.create" in _audit_actions(uuid.UUID(tenant_id))

    resp = client.get("/tenants")
    assert resp.status_code == 200
    assert any(t["slug"] == "store-new" for t in resp.json())


def test_creating_a_duplicate_slug_is_rejected(client, seeded) -> None:
    login_as(client, "root@example.com")
    client.post("/tenants", json={"slug": "store-dup", "name": "First"})
    resp = client.post("/tenants", json={"slug": "store-dup", "name": "Second"})
    assert resp.status_code == 409


def test_non_superadmin_cannot_create_tenants(client, seeded) -> None:
    login_as(client, "owner_a@example.com")
    resp = client.post("/tenants", json={"slug": "store-x", "name": "Store X"})
    assert resp.status_code == 403


def test_adapter_config_round_trip_never_returns_the_plaintext_secret(client, seeded) -> None:
    tenant_a = str(seeded["tenant_a"])
    login_as(client, "owner_a@example.com")

    resp = client.put(
        f"/tenants/{tenant_a}/adapter-config",
        json={
            "platform": "prestashop",
            "base_url": "https://store-a.example/api",
            "api_key": "super-secret-webservice-key",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_key"] != "super-secret-webservice-key"
    assert "super-secret-webservice-key" not in resp.text
    assert body["base_url"] == "https://store-a.example/api"

    resp = client.get(f"/tenants/{tenant_a}/adapter-config")
    assert resp.status_code == 200
    assert "super-secret-webservice-key" not in resp.text

    assert "adapter_config.upsert" in _audit_actions(seeded["tenant_a"])


def test_adapter_config_is_encrypted_at_rest_not_just_masked_in_the_api(client, seeded) -> None:
    tenant_a = str(seeded["tenant_a"])
    login_as(client, "owner_a@example.com")
    client.put(
        f"/tenants/{tenant_a}/adapter-config",
        json={"platform": "prestashop", "base_url": "https://store-a.example/api", "api_key": "the-real-secret"},
    )

    from tenancy_db.models.tenant import TenantAdapterConfig

    with session_scope() as db:
        stmt = sa.select(TenantAdapterConfig).where(TenantAdapterConfig.tenant_id == seeded["tenant_a"])
        row = db.scalars(stmt).first()
        assert row.api_key_encrypted != "the-real-secret"
        from tenancy_db.crypto import decrypt_secret

        assert decrypt_secret(row.api_key_encrypted) == "the-real-secret"


def test_analyst_cannot_write_adapter_config(client, seeded) -> None:
    tenant_a = str(seeded["tenant_a"])
    login_as(client, "analyst_a@example.com")
    resp = client.put(
        f"/tenants/{tenant_a}/adapter-config",
        json={"platform": "mock", "base_url": "https://x.example", "api_key": "k"},
    )
    assert resp.status_code == 403


def test_widget_key_issue_and_revoke(client, seeded) -> None:
    tenant_a = str(seeded["tenant_a"])
    login_as(client, "owner_a@example.com")

    resp = client.post(f"/tenants/{tenant_a}/widget-keys", json={"allowed_origins": ["https://store-a.example"]})
    assert resp.status_code == 201
    key = resp.json()
    assert key["public_key"].startswith("pk_live_")
    assert key["is_active"] is True

    resp = client.get(f"/tenants/{tenant_a}/widget-keys")
    assert any(k["id"] == key["id"] for k in resp.json())

    resp = client.delete(f"/tenants/{tenant_a}/widget-keys/{key['id']}")
    assert resp.status_code == 204

    resp = client.get(f"/tenants/{tenant_a}/widget-keys")
    revoked = next(k for k in resp.json() if k["id"] == key["id"])
    assert revoked["is_active"] is False

    actions = _audit_actions(seeded["tenant_a"])
    assert "widget_key.issue" in actions
    assert "widget_key.revoke" in actions


def test_promo_rule_upsert_and_list(client, seeded) -> None:
    tenant_a = str(seeded["tenant_a"])
    login_as(client, "admin_a@example.com")

    resp = client.put(
        f"/tenants/{tenant_a}/promo-rules/welcome10",
        json={"condition": "first_order", "target_code": "WELCOME10", "priority": 10},
    )
    assert resp.status_code == 200
    assert resp.json()["rule_id"] == "welcome10"

    resp = client.get(f"/tenants/{tenant_a}/promo-rules")
    assert any(r["rule_id"] == "welcome10" for r in resp.json())
    assert "promo_rule.upsert" in _audit_actions(seeded["tenant_a"])


def test_analyst_can_read_promo_rules_but_not_write_them(client, seeded) -> None:
    tenant_a = str(seeded["tenant_a"])
    login_as(client, "analyst_a@example.com")
    assert client.get(f"/tenants/{tenant_a}/promo-rules").status_code == 200
    resp = client.put(
        f"/tenants/{tenant_a}/promo-rules/x",
        json={"condition": "c", "target_code": "X"},
    )
    assert resp.status_code == 403
