"""Grants (or updates) one admin's membership on one tenant.

Fills the gap `backoffice/README.md`'s "Known gaps" section already names: no user-invite
flow — admin accounts and memberships are created directly against the database. This is
that one-off script, made reusable: `scripts/bootstrap.py` creates a tenant and a
superadmin, but a superadmin has no memberships and so never sees a tenant switcher in the
dashboard; a scoped operator with memberships on more than one tenant needs this to get
access to the second (and third, ...) one.

Idempotent — safe to re-run: updates the role if the membership already exists.

Usage:
    python -m scripts.grant_membership \
        --admin-email you@example.com --tenant-slug store-two --role owner
"""

from __future__ import annotations

import argparse
import sys

from tenancy_db.engine import get_engine, session_scope
from tenancy_db.models.admin import AdminRole
from tenancy_db.repositories import AdminUserRepository, TenantMembershipRepository, TenantRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--role", choices=[r.value for r in AdminRole], default=AdminRole.OWNER.value)
    args = parser.parse_args(argv)

    if get_engine() is None:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    with session_scope() as db:
        admin = AdminUserRepository(db).get_by_email(args.admin_email)
        if admin is None:
            print(f"No admin user with email {args.admin_email!r} — create one first (scripts/bootstrap.py).", file=sys.stderr)
            return 1

        tenant = TenantRepository(db).get_by_slug(args.tenant_slug)
        if tenant is None:
            print(f"No tenant with slug {args.tenant_slug!r}.", file=sys.stderr)
            return 1

        memberships = TenantMembershipRepository(db)
        role = AdminRole(args.role)
        existing = memberships.get_role(tenant.id, admin.id)
        if existing is None:
            memberships.add_member(tenant.id, admin.id, role)
            print(f"Granted {args.admin_email!r} {role.value!r} on tenant {args.tenant_slug!r}.")
        else:
            memberships.set_role(tenant.id, admin.id, role)
            print(f"Updated {args.admin_email!r}'s role on tenant {args.tenant_slug!r}: {existing.value!r} -> {role.value!r}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
