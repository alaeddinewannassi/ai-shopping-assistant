"""Backoffice self-audit (T509) — records every mutating admin action.

Distinct from chatbot/backend's `assistant_event` stream: this tracks what *operators* did
(who changed a tenant's adapter config, issued/revoked a widget key, edited a promo rule),
not what the assistant did on a shopper's behalf.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session
from tenancy_db.models.admin import AdminAudit


def record(
    db: Session,
    *,
    admin_user_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
    action: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AdminAudit(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            action=action,
            target=target,
            details=details or {},
        )
    )
