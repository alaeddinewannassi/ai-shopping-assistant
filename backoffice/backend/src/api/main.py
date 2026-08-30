"""Backoffice admin/analytics API — a separate FastAPI service from chatbot/backend/ (T501).

Owns writes to the shared tenancy-db package: tenant CRUD, adapter/LLM config, promo rules,
widget keys, admin users/roles (Phase 5, specs/002-backoffice-analytics/plan.md). Auth
(T502), RBAC (T503), and most admin endpoints (T505-T509) are wired in below — see
specs/002-backoffice-analytics/contracts/admin-api.yaml for exactly what's covered and what
isn't (TOTP/MFA, adapter "Test connection", user invitations, CSV export).

Relationship to chatbot/backend/: 1 backoffice deployment can administer many tenants, each
of which chatbot/backend/ can serve to many storefronts (one X-Assistant-Key per site) — the
two services share no process and communicate only through the tenancy-db database, never
by importing each other's code or calling each other's HTTP APIs.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from tenancy_db import engine as db_engine

from src.api.routes import analytics, auth, sessions, tenants

app = FastAPI(title="AI Shopping Assistant — Backoffice API", version="0.1.0")

# The backoffice/frontend/ SPA is the only intended caller — no public widget embeds this
# API, so origins come from an explicit allowlist (ADMIN_CORS_ORIGINS, .env.example) rather
# than the wildcard chatbot/backend/ uses for its public widget-facing CORS. Unset ->
# no origin is allowed, failing closed rather than defaulting to open. Credentials must be
# allowed — auth is httpOnly cookies (src/auth/tokens.py), not a bearer header.
_allowed_origins = [
    origin.strip()
    for origin in os.environ.get("ADMIN_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(analytics.router)
app.include_router(sessions.router)


@app.get("/health")
def health() -> dict[str, str]:
    db_status = db_engine.check_health()
    overall = "ok" if db_status == "ok" else "degraded"
    return {"status": overall, "database": db_status}
