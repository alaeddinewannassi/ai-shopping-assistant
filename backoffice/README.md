# Backoffice

The multi-tenant admin/analytics project (specs/002-backoffice-analytics) — separate from
`chatbot/` (the shopper-facing service). The two share no process and import none of each
other's code; the only thing they have in common is the `tenancy-db` package one directory
up, which `backoffice/backend` writes to and `chatbot/backend` only reads.

```
backend/    FastAPI admin/analytics API (auth, RBAC, tenant CRUD, analytics queries)
frontend/   React SPA — the actual dashboard (see frontend/README.md)
```

## Setup (local dev)

```bash
# 1. A Postgres database (or docker compose up postgres from ../docker/)
export DATABASE_URL=postgresql+psycopg://assistant:assistant@localhost:5432/assistant

# 2. Install the shared tenancy-db package, then this backend
cd ../tenancy-db && python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]' && deactivate
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ../../tenancy-db && pip install -e '.[dev]'
cp .env.example .env   # fill in APP_ENCRYPTION_KEY, JWT_SECRET, ADMIN_CORS_ORIGINS

# 3. Bootstrap the first tenant + superadmin from an existing chatbot/backend/.env
#    (see "Migrating an existing single-tenant deployment" below) — or, for a fresh
#    install with no legacy .env to migrate, just create a superadmin:
python -m scripts.bootstrap --superadmin-email you@example.com --superadmin-password 'change-me'

# 4. Run it
uvicorn src.api.main:app --reload --port 8001

# 5. Frontend (separate terminal)
cd ../frontend && npm install && npm run dev   # http://localhost:5173
```

`APP_ENCRYPTION_KEY` must be the **same value** `chatbot/backend` uses — both decrypt the
same `tenant_adapter_config`/`tenant_llm_config` rows. Generate one with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Migrating an existing single-tenant deployment

If you already run `chatbot/backend` in its pre-multi-tenant mode (a `.env` with
`PRESTASHOP_BASE_URL`/`PRESTASHOP_API_KEY`/`LLM_PROVIDER`, no `DATABASE_URL`), running
`chatbot/backend`'s own tests or turns never required this project at all — it still works
unchanged (`legacy_env_tenant_config()`, plan.md D2). This project's bootstrap script is
how you *opt in* to the backoffice without breaking that deployment:

```bash
cd backoffice/backend
source .venv/bin/activate
python -m scripts.bootstrap \
  --superadmin-email you@example.com \
  --superadmin-password 'change-me' \
  --promo-rules-json ../../chatbot/backend/src/promo/rules.json
```

Run this with the **same environment** `chatbot/backend` currently uses (same
`PRESTASHOP_*`/`LLM_*` values — the script reads them the same way `chatbot/backend` does)
plus `DATABASE_URL` pointing at a real Postgres. It creates a `Tenant` row matching
`DEFAULT_TENANT_SLUG` (default `"default"`), migrates the adapter/LLM config into it, and
seeds promo rules **only if you pass `--promo-rules-json`** — a real, sharp edge: the
moment this tenant row exists, `chatbot/backend` switches from its env-based fallback to
this DB-backed tenant, so skipping the promo-rules flag silently loses WELCOME10/BIGCART15
(or whatever rules the legacy deployment had). The script prints a warning if you skip it,
but doesn't fail — you may genuinely want a clean slate.

After bootstrapping, `chatbot/backend` needs nothing further — it already resolves
`DEFAULT_TENANT_SLUG` against the database automatically once `DATABASE_URL` is set for it
too (this only helps if `chatbot/backend` and `backoffice/backend` point at the *same*
`DATABASE_URL`).

## Onboarding a new tenant (not a migration — a brand-new merchant)

1. Log into the frontend as a superadmin, go to **All tenants**, create one (slug + name).
2. Open the new tenant, go to **Settings**:
   - **Store connection** — platform, base URL, API key. There is no "Test connection"
     button yet (an open architecture question, see plan.md's Phase 5 notes) — verify by
     having the merchant's storefront widget actually try a search.
   - **LLM provider** — `rule-based-stub` needs nothing else and works immediately; any
     other provider needs its own API key.
   - **Widget keys** — issue one, copy the embed snippet shown, hand it to the merchant to
     paste into their storefront.
   - **Promo rules** — add any promo strategy rules for this tenant (empty by default,
     unlike the bootstrap-migrated default tenant).
3. Confirm activity by watching **Overview**/**Sessions** once the widget is live.

## Known gaps (see specs/002-backoffice-analytics/plan.md for the full account)

No adapter "Test connection", no user-invite flow (admin accounts are created directly
against the database — `scripts/bootstrap.py` or a one-off script using the same
repositories), no CSV export, no retention/privacy controls, no TOTP/MFA, no refresh-token
revocation list.
