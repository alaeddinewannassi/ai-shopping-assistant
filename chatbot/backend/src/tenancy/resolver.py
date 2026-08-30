"""Resolves an inbound request to its TenantRuntime (T202).

Reads the widget's `X-Assistant-Key` header. No key — or DATABASE_URL unset entirely, or
set but the default tenant hasn't been bootstrapped yet (T702) — falls back to
`DEFAULT_TENANT_SLUG`'s legacy env-driven config, so every pre-002 deployment and the whole
existing test suite keep working unchanged (plan.md D2). A key that doesn't resolve to an
active widget key, or resolves to a suspended tenant, is rejected outright rather than
silently falling back — an unrecognized key must never quietly grant default-tenant access.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException
from tenancy_db.engine import is_configured, session_scope
from tenancy_db.repositories import WidgetKeyRepository

from src.tenancy.config import (
    TenantConfig,
    TenantNotFoundError,
    TenantSuspendedError,
    legacy_env_tenant_config,
    load_tenant_config,
)
from src.tenancy.runtime import TenantRuntime, build_tenant_runtime, get_or_build_runtime


def _default_slug() -> str:
    return os.environ.get("DEFAULT_TENANT_SLUG", "default")


def resolve_tenant_config(
    assistant_key: str | None = None, *, origin: str | None = None
) -> TenantConfig:
    default_slug = _default_slug()

    if not is_configured():
        return legacy_env_tenant_config(default_slug)

    with session_scope() as db:
        if db is None:
            return legacy_env_tenant_config(default_slug)

        if assistant_key:
            widget_key = WidgetKeyRepository(db).get_by_public_key(assistant_key)
            if widget_key is None:
                raise HTTPException(status_code=401, detail="Unknown or revoked assistant key")
            if origin and widget_key.allowed_origins and origin not in widget_key.allowed_origins:
                raise HTTPException(status_code=403, detail="Origin not allowed for this assistant key")
            try:
                return load_tenant_config(db, str(widget_key.tenant_id), by_slug=False)
            except TenantNotFoundError as exc:
                # A widget_key row pointing at a deleted tenant is a data-integrity bug, not
                # a client error — surface it distinctly rather than as a generic 401/403.
                raise HTTPException(status_code=500, detail="Assistant key has no owning tenant") from exc
            except TenantSuspendedError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc

        try:
            return load_tenant_config(db, default_slug, by_slug=True)
        except TenantNotFoundError:
            return legacy_env_tenant_config(default_slug)
        except TenantSuspendedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


def resolve_tenant_runtime(
    x_assistant_key: str | None = Header(default=None), origin: str | None = Header(default=None)
) -> TenantRuntime:
    """FastAPI dependency: `def chat(request: ChatRequest, runtime: TenantRuntime =
    Depends(resolve_tenant_runtime))`."""
    config = resolve_tenant_config(x_assistant_key, origin=origin)
    return get_or_build_runtime(str(config.tenant_id), lambda: build_tenant_runtime(config))
