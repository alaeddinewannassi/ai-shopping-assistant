"""Login/refresh/logout/me (T502)."""

from __future__ import annotations

from tests.conftest import PASSWORD, login_as


def test_login_succeeds_and_sets_cookies(client, seeded) -> None:
    resp = client.post("/auth/login", json={"email": "owner_a@example.com", "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "owner_a@example.com"
    assert any(m["role"] == "owner" for m in body["memberships"])
    assert "assistant_admin_access" in resp.cookies


def test_login_fails_with_wrong_password(client, seeded) -> None:
    resp = client.post("/auth/login", json={"email": "owner_a@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_login_fails_for_unknown_email(client, seeded) -> None:
    resp = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert resp.status_code == 401


def test_login_rejects_disabled_account(client, seeded) -> None:
    resp = client.post("/auth/login", json={"email": "disabled@example.com", "password": PASSWORD})
    assert resp.status_code == 403


def test_me_requires_authentication(client, seeded) -> None:
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_the_logged_in_user_after_login(client, seeded) -> None:
    login_as(client, "owner_a@example.com")
    resp = client.get("/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "owner_a@example.com"


def test_logout_clears_the_session(client, seeded) -> None:
    login_as(client, "owner_a@example.com")
    assert client.get("/auth/me").status_code == 200

    resp = client.post("/auth/logout")
    assert resp.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_refresh_issues_a_new_access_token(client, seeded) -> None:
    login_as(client, "owner_a@example.com")
    resp = client.post("/auth/refresh")
    assert resp.status_code == 200
    assert client.get("/auth/me").status_code == 200


def test_refresh_without_a_refresh_cookie_is_unauthorized(client, seeded) -> None:
    resp = client.post("/auth/refresh")
    assert resp.status_code == 401
