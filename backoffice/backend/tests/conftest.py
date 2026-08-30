"""Shared fixtures: a seeded tenancy database (2 tenants, 5 admin users covering every
role + an outsider with no membership + a superadmin) and a TestClient against it.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from tenancy_db.base import Base
from tenancy_db.crypto import reset_key_cache
from tenancy_db.engine import reset_engine
from tenancy_db.models.admin import AdminRole, AdminUserStatus
from tenancy_db.repositories import (
    AdminUserRepository,
    TenantMembershipRepository,
    TenantRepository,
)

from src.auth.passwords import hash_password

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    db_path = tmp_path / "backoffice_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-not-for-production")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", "test-encryption-key-not-for-production")
    monkeypatch.setenv("COOKIE_SECURE", "false")  # TestClient uses plain http
    monkeypatch.setenv("ADMIN_CORS_ORIGINS", "http://localhost:5173")
    reset_engine()
    reset_key_cache()

    from tenancy_db.engine import get_engine

    engine = get_engine()
    Base.metadata.create_all(engine)

    from tenancy_db.engine import session_scope

    ids: dict[str, uuid.UUID] = {}
    with session_scope() as db:
        tenants = TenantRepository(db)
        users = AdminUserRepository(db)
        memberships = TenantMembershipRepository(db)

        tenant_a = tenants.create("store-a", "Store A")
        tenant_b = tenants.create("store-b", "Store B")

        role_users = {
            "owner_a": AdminRole.OWNER,
            "admin_a": AdminRole.ADMIN,
            "analyst_a": AdminRole.ANALYST,
            "support_a": AdminRole.SUPPORT,
        }
        for name, role in role_users.items():
            user = users.create(f"{name}@example.com", hash_password(PASSWORD), name)
            memberships.add_member(tenant_a.id, user.id, role)
            ids[name] = user.id

        outsider = users.create("outsider@example.com", hash_password(PASSWORD), "outsider")
        ids["outsider"] = outsider.id

        superadmin = users.create(
            "root@example.com", hash_password(PASSWORD), "root", is_superadmin=True
        )
        ids["superadmin"] = superadmin.id

        disabled = users.create("disabled@example.com", hash_password(PASSWORD), "disabled")
        disabled.status = AdminUserStatus.DISABLED
        ids["disabled"] = disabled.id

        ids["tenant_a"] = tenant_a.id
        ids["tenant_b"] = tenant_b.id

    yield ids

    reset_engine()
    reset_key_cache()


@pytest.fixture
def client(seeded):
    from src.api.main import app

    return TestClient(app)


def login_as(client: TestClient, email: str) -> TestClient:
    resp = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return client
