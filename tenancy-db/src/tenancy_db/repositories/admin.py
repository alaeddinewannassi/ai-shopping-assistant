"""Admin user + tenant-membership repositories (T106).

`AdminUserRepository` is not tenant-scoped (an admin account can belong to many tenants);
`TenantMembershipRepository` is the join and IS scoped, since "which admins can see this
tenant's data" is exactly the isolation question T107 tests.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from tenancy_db.models.admin import AdminRole, AdminUser, TenantMembership
from tenancy_db.tenant_context import scoped_to_tenant


class AdminUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, email: str, password_hash: str, name: str, *, is_superadmin: bool = False
    ) -> AdminUser:
        user = AdminUser(
            email=email, password_hash=password_hash, name=name, is_superadmin=is_superadmin
        )
        self._session.add(user)
        self._session.flush()
        return user

    def get_by_email(self, email: str) -> AdminUser | None:
        stmt = sa.select(AdminUser).where(AdminUser.email == email)
        return self._session.scalars(stmt).first()

    def get_by_id(self, admin_user_id: uuid.UUID) -> AdminUser | None:
        return self._session.get(AdminUser, admin_user_id)


class TenantMembershipRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_member(self, tenant_id: uuid.UUID, admin_user_id: uuid.UUID, role: AdminRole) -> TenantMembership:
        with scoped_to_tenant(self._session, tenant_id):
            membership = TenantMembership(tenant_id=tenant_id, admin_user_id=admin_user_id, role=role)
            self._session.add(membership)
            self._session.flush()
            return membership

    def list_members(self, tenant_id: uuid.UUID) -> list[TenantMembership]:
        with scoped_to_tenant(self._session, tenant_id):
            stmt = sa.select(TenantMembership).where(TenantMembership.tenant_id == tenant_id)
            return list(self._session.scalars(stmt).all())

    def get_role(self, tenant_id: uuid.UUID, admin_user_id: uuid.UUID) -> AdminRole | None:
        with scoped_to_tenant(self._session, tenant_id):
            stmt = sa.select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.admin_user_id == admin_user_id,
            )
            membership = self._session.scalars(stmt).first()
            return membership.role if membership else None

    def list_members_for_user(self, admin_user_id: uuid.UUID) -> list[TenantMembership]:
        """Deliberately cross-tenant — answers "which tenants can this admin see," which is
        inherently not scoped to any single tenant (GET /auth/me, the backoffice's tenant
        switcher). Every *other* method on this class is per-tenant on purpose; this one
        isn't, for the same reason WidgetKeyRepository.get_by_public_key isn't."""
        stmt = sa.select(TenantMembership).where(TenantMembership.admin_user_id == admin_user_id)
        return list(self._session.scalars(stmt).all())
