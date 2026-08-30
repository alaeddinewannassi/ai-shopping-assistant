"""Authz matrix (T511): each role x each distinct RBAC boundary, including negative cases.

Not exhaustively every one of the 17 endpoints x every role (that's a lot of near-identical
assertions) — instead, one representative endpoint per distinct dependency
(`require_tenant_role(*MANAGE_TENANT)`, `*VIEW_ANALYTICS`, `*VIEW_SESSIONS`, `*ANY_MEMBER`,
`require_superadmin`) is exercised against every role in the seeded fixture, since that
dependency — not the specific route — is what actually enforces access. A regression in any
one of these five dependency functions would be caught here regardless of which endpoint
happens to use it.
"""

from __future__ import annotations

from tests.conftest import login_as

_ANALYTICS_QS = "?start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z"

# (email, allowed_on_manage_tenant, allowed_on_view_analytics, allowed_on_view_sessions,
#  allowed_on_any_member, allowed_superadmin_only)
_ROLE_MATRIX = [
    ("owner_a@example.com", True, True, True, True, False),
    ("admin_a@example.com", True, True, True, True, False),
    ("analyst_a@example.com", False, True, True, True, False),
    ("support_a@example.com", False, False, True, True, False),
    ("outsider@example.com", False, False, False, False, False),
    ("root@example.com", True, True, True, True, True),  # superadmin bypasses everything
]


def test_authz_matrix_across_every_role(client, seeded) -> None:
    tenant_a = str(seeded["tenant_a"])

    for email, manage, analytics, sessions, any_member, superadmin_only in _ROLE_MATRIX:
        login_as(client, email)

        # MANAGE_TENANT-gated write: PATCH /tenants/{id} with a no-op empty body.
        resp = client.patch(f"/tenants/{tenant_a}", json={})
        assert resp.status_code != 403 if manage else resp.status_code == 403, (
            f"{email}: PATCH /tenants/{{id}} expected manage={manage}, got {resp.status_code}"
        )

        # VIEW_ANALYTICS-gated read.
        resp = client.get(f"/tenants/{tenant_a}/analytics/overview{_ANALYTICS_QS}")
        assert resp.status_code != 403 if analytics else resp.status_code == 403, (
            f"{email}: GET analytics/overview expected analytics={analytics}, got {resp.status_code}"
        )

        # VIEW_SESSIONS-gated read.
        resp = client.get(f"/tenants/{tenant_a}/sessions")
        assert resp.status_code != 403 if sessions else resp.status_code == 403, (
            f"{email}: GET sessions expected sessions={sessions}, got {resp.status_code}"
        )

        # ANY_MEMBER-gated read.
        resp = client.get(f"/tenants/{tenant_a}")
        assert resp.status_code != 403 if any_member else resp.status_code == 403, (
            f"{email}: GET tenant detail expected any_member={any_member}, got {resp.status_code}"
        )

        # Superadmin-only: cross-tenant tenant list.
        resp = client.get("/tenants")
        assert resp.status_code != 403 if superadmin_only else resp.status_code == 403, (
            f"{email}: GET /tenants (list) expected superadmin={superadmin_only}, got {resp.status_code}"
        )

        client.post("/auth/logout")


def test_unauthenticated_requests_are_401_not_403(client, seeded) -> None:
    """403 means "we know who you are, you're just not allowed" — an unauthenticated caller
    must get 401, never leak into the 403 path (which would imply valid-but-insufficient
    creds rather than no creds at all)."""
    tenant_a = str(seeded["tenant_a"])
    for path in (f"/tenants/{tenant_a}", "/tenants", f"/tenants/{tenant_a}/sessions"):
        resp = client.get(path)
        assert resp.status_code == 401, f"{path}: expected 401, got {resp.status_code}"


def test_a_role_on_one_tenant_grants_nothing_on_another(client, seeded) -> None:
    """owner_a is owner on tenant_a and has NO membership on tenant_b — must be 403 there,
    not silently treated as having some default role."""
    login_as(client, "owner_a@example.com")
    tenant_b = str(seeded["tenant_b"])
    resp = client.get(f"/tenants/{tenant_b}")
    assert resp.status_code == 403
    resp = client.patch(f"/tenants/{tenant_b}", json={})
    assert resp.status_code == 403
