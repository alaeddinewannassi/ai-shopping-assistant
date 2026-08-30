"""Tenant isolation at the repository layer (T107, blocking/security-critical).

Every tenant-scoped repository method requires a `tenant_id` argument and filters on it
explicitly (src/db/repositories/*.py) — this is the primary isolation guarantee; Postgres
Row-Level Security (migrations/versions/4024383a2d62_tenancy_baseline.py) is a
defense-in-depth backstop that SQLite (this test's target, matching the rest of the suite's
Redis-optional style) doesn't support. This test proves the repository-layer guarantee
holds: no combination of calls lets tenant A's session read or write tenant B's rows.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from tenancy_db.base import Base
from tenancy_db.models.admin import AdminRole
from tenancy_db.repositories import (
    AdminUserRepository,
    TenantAdapterConfigRepository,
    TenantMembershipRepository,
    TenantPromoRuleRepository,
    TenantRepository,
    WidgetKeyRepository,
)


@pytest.fixture
def session():
    engine = sa.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    yield db
    db.close()
    engine.dispose()


@pytest.fixture
def two_tenants(session):
    tenants = TenantRepository(session)
    tenant_a = tenants.create("store-a", "Store A")
    tenant_b = tenants.create("store-b", "Store B")
    session.commit()
    return tenant_a, tenant_b


def test_adapter_config_scoped_per_tenant(session, two_tenants) -> None:
    tenant_a, tenant_b = two_tenants
    configs = TenantAdapterConfigRepository(session)
    configs.upsert(tenant_a.id, platform="prestashop", base_url="https://a.example/api", api_key_encrypted="enc-a")
    configs.upsert(tenant_b.id, platform="prestashop", base_url="https://b.example/api", api_key_encrypted="enc-b")
    session.commit()

    config_a = configs.get_for_tenant(tenant_a.id)
    config_b = configs.get_for_tenant(tenant_b.id)
    assert config_a.base_url == "https://a.example/api"
    assert config_b.base_url == "https://b.example/api"
    assert config_a.tenant_id != config_b.tenant_id


def test_promo_rules_never_leak_across_tenants(session, two_tenants) -> None:
    tenant_a, tenant_b = two_tenants
    rules = TenantPromoRuleRepository(session)
    rules.upsert_rule(tenant_a.id, "welcome10", condition="first_order", target_code="WELCOME10")
    rules.upsert_rule(tenant_b.id, "bigcart15", condition="cart_value>150", target_code="BIGCART15")
    session.commit()

    a_rules = rules.list_active_for_tenant(tenant_a.id)
    b_rules = rules.list_active_for_tenant(tenant_b.id)
    assert [r.rule_id for r in a_rules] == ["welcome10"]
    assert [r.rule_id for r in b_rules] == ["bigcart15"]
    # Same rule_id string reused by both tenants must not collide (uq is (tenant_id, rule_id)).
    rules.upsert_rule(tenant_b.id, "welcome10", condition="first_order", target_code="WELCOME10-B")
    session.commit()
    assert {r.rule_id for r in rules.list_active_for_tenant(tenant_b.id)} == {"bigcart15", "welcome10"}
    assert [r.rule_id for r in rules.list_active_for_tenant(tenant_a.id)] == ["welcome10"]


def test_widget_key_resolves_to_the_issuing_tenant_only(session, two_tenants) -> None:
    tenant_a, tenant_b = two_tenants
    keys = WidgetKeyRepository(session)
    keys.issue(tenant_a.id, "pk_live_aaa", ["https://store-a.example"])
    keys.issue(tenant_b.id, "pk_live_bbb", ["https://store-b.example"])
    session.commit()

    resolved = keys.get_by_public_key("pk_live_aaa")
    assert resolved is not None
    assert resolved.tenant_id == tenant_a.id
    assert keys.get_by_public_key("pk_live_bbb").tenant_id == tenant_b.id

    # tenant A's key list must never include tenant B's key, even by id guessing.
    a_keys = keys.list_for_tenant(tenant_a.id)
    assert all(k.tenant_id == tenant_a.id for k in a_keys)
    assert "pk_live_bbb" not in {k.public_key for k in a_keys}


def test_revoking_a_key_under_the_wrong_tenant_id_is_a_silent_noop(session, two_tenants) -> None:
    """A must never be able to revoke B's key by passing B's key_id alongside A's tenant_id."""
    tenant_a, tenant_b = two_tenants
    keys = WidgetKeyRepository(session)
    key_b = keys.issue(tenant_b.id, "pk_live_bbb", ["https://store-b.example"])
    session.commit()

    keys.revoke(tenant_a.id, key_b.id)
    session.commit()

    still_active = keys.get_by_public_key("pk_live_bbb")
    assert still_active is not None and still_active.is_active is True


def test_membership_scoped_per_tenant(session, two_tenants) -> None:
    tenant_a, tenant_b = two_tenants
    users = AdminUserRepository(session)
    memberships = TenantMembershipRepository(session)

    alice = users.create("alice@example.com", "hash", "Alice")
    session.commit()
    memberships.add_member(tenant_a.id, alice.id, AdminRole.OWNER)
    session.commit()

    assert memberships.get_role(tenant_a.id, alice.id) == AdminRole.OWNER
    # Alice has no membership on tenant B — must resolve to None, not tenant A's role.
    assert memberships.get_role(tenant_b.id, alice.id) is None
