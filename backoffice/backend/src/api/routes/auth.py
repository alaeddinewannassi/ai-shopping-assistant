"""Auth endpoints: login/refresh/logout/me (T502).

No TOTP/MFA (contract note in admin-api.yaml) and no server-side refresh-token revocation
list — see src/auth/tokens.py's docstring for that gap.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from tenancy_db.models.admin import AdminUserStatus
from tenancy_db.repositories import AdminUserRepository, TenantMembershipRepository, TenantRepository

from src.auth.dependencies import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    get_current_admin_user,
    get_db,
)
from src.auth.passwords import verify_password
from src.auth.tokens import TokenError, create_access_token, create_refresh_token, decode_token

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "true").lower() != "false"


def _set_access_cookie(response: Response, admin_user_id) -> None:
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        create_access_token(admin_user_id),
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        max_age=15 * 60,
    )


def _set_session_cookies(response: Response, admin_user_id) -> None:
    _set_access_cookie(response, admin_user_id)
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        create_refresh_token(admin_user_id),
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        max_age=30 * 24 * 60 * 60,
        path="/auth/refresh",
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MembershipOut(BaseModel):
    tenant_id: str
    tenant_name: str
    tenant_slug: str
    role: str


class AdminUserOut(BaseModel):
    id: str
    email: str
    name: str
    is_superadmin: bool
    memberships: list[MembershipOut]


def _to_admin_user_out(db: Session, admin_user) -> AdminUserOut:
    memberships = TenantMembershipRepository(db).list_members_for_user(admin_user.id)
    tenant_repo = TenantRepository(db)
    out = []
    for m in memberships:
        tenant = tenant_repo.get_by_id(m.tenant_id)
        out.append(
            MembershipOut(
                tenant_id=str(m.tenant_id),
                tenant_name=tenant.name if tenant else str(m.tenant_id),
                tenant_slug=tenant.slug if tenant else "",
                role=m.role.value,
            )
        )
    return AdminUserOut(
        id=str(admin_user.id),
        email=admin_user.email,
        name=admin_user.name,
        is_superadmin=admin_user.is_superadmin,
        memberships=out,
    )


@router.post("/login", response_model=AdminUserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> AdminUserOut:
    admin_user = AdminUserRepository(db).get_by_email(payload.email)
    if admin_user is None or not verify_password(payload.password, admin_user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if admin_user.status != AdminUserStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Account disabled")

    _set_session_cookies(response, admin_user.id)
    return _to_admin_user_out(db, admin_user)


@router.post("/refresh")
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if refresh_token is None:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        admin_user_id = decode_token(refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    admin_user = AdminUserRepository(db).get_by_id(admin_user_id)
    if admin_user is None or admin_user.status != AdminUserStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="Not authenticated")

    _set_access_cookie(response, admin_user.id)
    return {"status": "refreshed"}


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME)
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/auth/refresh")


@router.get("/me", response_model=AdminUserOut)
def me(admin_user=Depends(get_current_admin_user), db: Session = Depends(get_db)) -> AdminUserOut:
    return _to_admin_user_out(db, admin_user)
