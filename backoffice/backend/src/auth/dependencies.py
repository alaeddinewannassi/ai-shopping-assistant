"""FastAPI auth/RBAC dependencies (T502/T503).

`get_current_admin_user` authenticates via the access-token cookie; `require_tenant_role`
is a dependency *factory* — `Depends(require_tenant_role(AdminRole.OWNER, AdminRole.ADMIN))`
— that additionally authorizes the authenticated user against the `tenant_id` path
parameter, bypassed entirely for superadmins.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session
from tenancy_db.engine import get_session
from tenancy_db.models.admin import AdminRole, AdminUser, AdminUserStatus
from tenancy_db.repositories import AdminUserRepository, TenantMembershipRepository

from src.auth.tokens import TokenError, decode_token

ACCESS_COOKIE_NAME = "assistant_admin_access"
REFRESH_COOKIE_NAME = "assistant_admin_refresh"


def get_db() -> Iterator[Session]:
    """Unlike chatbot/backend's `session_scope()`, this API has no legitimate reason to
    run with no database at all — every endpoint reads or writes tenancy data. A missing/
    unreachable database is a 503, not a silent no-op."""
    session = get_session()
    if session is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_admin_user(
    db: Session = Depends(get_db),
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
) -> AdminUser:
    if access_token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        admin_user_id = decode_token(access_token, expected_type="access")
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    admin_user = AdminUserRepository(db).get_by_id(admin_user_id)
    if admin_user is None or admin_user.status != AdminUserStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return admin_user


def require_tenant_role(*roles: AdminRole):
    """Returns a FastAPI dependency requiring the authenticated user to hold one of `roles`
    on the `tenant_id` path parameter — or to be a superadmin, which bypasses membership
    entirely. Raises 403 (not 404) for a real-but-inaccessible tenant, matching the
    admin-api.yaml contract — existence isn't leaked to a user with no membership."""

    def _check(
        tenant_id: uuid.UUID,
        admin_user: AdminUser = Depends(get_current_admin_user),
        db: Session = Depends(get_db),
    ) -> AdminUser:
        if admin_user.is_superadmin:
            return admin_user
        role = TenantMembershipRepository(db).get_role(tenant_id, admin_user.id)
        if role is None or role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role for this tenant")
        return admin_user

    return _check


def require_superadmin(admin_user: AdminUser = Depends(get_current_admin_user)) -> AdminUser:
    if not admin_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Superadmin only")
    return admin_user


# Convenience role groups matching admin-api.yaml's per-endpoint role notes.
MANAGE_TENANT = (AdminRole.OWNER, AdminRole.ADMIN)
VIEW_ANALYTICS = (AdminRole.OWNER, AdminRole.ADMIN, AdminRole.ANALYST)
VIEW_SESSIONS = (AdminRole.OWNER, AdminRole.ADMIN, AdminRole.ANALYST, AdminRole.SUPPORT)
ANY_MEMBER = VIEW_SESSIONS
