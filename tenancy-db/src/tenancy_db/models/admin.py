"""Backoffice operator accounts + access control (T104).

`AdminUser` accounts are distinct from shopper `ConversationSession`s — no shopper-facing
code path ever reads or writes these tables. `TenantMembership` is the RBAC join
(admin user x tenant x role, T503); `AdminAudit` is the backoffice auditing *itself*
(T509), separate from `assistant_event`'s shopper-facing audit trail (T301).
"""

from __future__ import annotations

import enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tenancy_db.base import Base, JSONType, created_at_column, uuid_pk


class AdminRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    ANALYST = "analyst"
    SUPPORT = "support"


class AdminUserStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[object] = uuid_pk()
    email: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    is_superadmin: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    status: Mapped[AdminUserStatus] = mapped_column(
        sa.Enum(AdminUserStatus, native_enum=False), nullable=False, default=AdminUserStatus.ACTIVE
    )
    created_at = created_at_column()

    memberships: Mapped[list[TenantMembership]] = relationship(
        back_populates="admin_user", cascade="all, delete-orphan"
    )


class TenantMembership(Base):
    __tablename__ = "tenant_membership"

    id: Mapped[object] = uuid_pk()
    admin_user_id: Mapped[object] = mapped_column(
        sa.Uuid, sa.ForeignKey("admin_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[object] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[AdminRole] = mapped_column(sa.Enum(AdminRole, native_enum=False), nullable=False)
    created_at = created_at_column()

    __table_args__ = (
        sa.UniqueConstraint("admin_user_id", "tenant_id", name="uq_tenant_membership_user_tenant"),
    )

    admin_user: Mapped[AdminUser] = relationship(back_populates="memberships")


class AdminAudit(Base):
    """Every mutating backoffice action (config change, key issue/revoke, role change) —
    T509. Distinct from `assistant_event`: this table tracks what *operators* did, not what
    the assistant did on a shopper's behalf."""

    __tablename__ = "admin_audit"

    id: Mapped[object] = uuid_pk()
    admin_user_id: Mapped[object | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("admin_user.id", ondelete="SET NULL"), nullable=True
    )
    tenant_id: Mapped[object | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("tenant.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    target: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    details: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    occurred_at = created_at_column()
