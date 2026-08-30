# AI Shopping Assistant

A conversational AI shopping assistant for e-commerce: natural-language product discovery,
cart management, checkout, and promo suggestions, built against a platform-agnostic
`CommerceAdapter` interface (reference implementation: PrestaShop). See
[`specs/001-ai-shopping-assistant/`](specs/001-ai-shopping-assistant/) for the full spec,
plan, research decisions, data model, and task breakdown this project was built from.

Every mutating action (add to cart, update/remove a line, apply a promo, checkout) is
gated by an explicit shopper confirmation before anything actually changes in the store —
see the Constitution's Principle III in
[`.specify/memory/constitution.md`](.specify/memory/constitution.md).

## Repository layout

Two independently deployable projects share one repo, plus a small data layer between them:

```
chatbot/
  backend/         FastAPI service: dialogue/agent logic, CommerceAdapter implementations
  widget/          Minimal embeddable chat widget (<assistant-chat-widget> custom element)
backoffice/
  backend/         FastAPI admin/analytics API — a separate process from chatbot/backend/
  frontend/        Admin dashboard SPA (specs/002-backoffice-analytics Phase 6)
tenancy-db/        Shared tenancy/admin data layer (models, repositories, Alembic migrations) —
                   chatbot/backend/ only reads through it, backoffice/backend/ owns writes
e2e/               Playwright suite driving a real conversation (Groq) through chatbot/
                   and verifying it in backoffice/ — see e2e/README.md
docker/            docker-compose.yml + PrestaShop reference-store fixture notes
specs/             Spec-kit artifacts: spec, plan, research, data model, contracts, tasks
```

One backoffice deployment administers many tenants; `chatbot/backend/` resolves each
request's tenant from its widget key, so one running chatbot service can serve many
storefronts. See [`specs/002-backoffice-analytics/plan.md`](specs/002-backoffice-analytics/plan.md)
for the full multi-tenancy design.

## Quickstart

The full walkthrough — bringing up PrestaShop/MySQL/Redis, configuring the Webservice API
key and demo promo codes, choosing an LLM provider, and validating each user story end to
end — lives in [`specs/001-ai-shopping-assistant/quickstart.md`](specs/001-ai-shopping-assistant/quickstart.md).
The short version:

```bash
# 1. Bring up the reference store (see docker/prestashop/README.md for the one-time
#    Admin setup: Webservice key, WELCOME10/BIGCART15 cart rules, checkout customer/address/carrier)
cd docker && docker compose up -d

# 2. Configure the assistant service
cp chatbot/backend/.env.example chatbot/backend/.env   # fill in PRESTASHOP_API_KEY, LLM_API_KEY, etc.

# 3. Install the shared tenancy-db package, then run the chatbot backend locally
#    (or `docker compose up -d assistant-service`)
cd tenancy-db && python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]' && deactivate
cd ../chatbot/backend
python -m venv .venv && source .venv/bin/activate
pip install -e ../../tenancy-db && pip install -e '.[dev]'
uvicorn src.api.chat:app --reload

# 4. Build and try the widget
cd ../widget
npm install && npm run build
# open a static HTML page with:
#   <script src="dist/assistant-widget.js"></script>
#   <assistant-chat-widget api-base="http://localhost:8000"></assistant-chat-widget>
```

For a hands-on, two-tenant walkthrough — two real PrestaShop stores with the widget
already embedded, one backoffice login managing both — see
[`docker/README-two-stores.md`](docker/README-two-stores.md).

The backoffice (admin dashboard) is a separate project with its own setup —
see [`backoffice/README.md`](backoffice/README.md), including how to migrate an existing
single-tenant deployment into it without losing its promo rules.

`LLM_PROVIDER=rule-based-stub` (the default for tests) needs no external API key at all and
runs a deterministic keyword matcher — good for exercising the full flow with zero cost or
network dependency before wiring up a real LLM.

## Running the tests

```bash
cd chatbot/backend
source .venv/bin/activate
pytest tests/unit tests/contract tests/integration
```

`tests/unit/` and most of `tests/integration/` need no external services (they run against
`MockAdapter`, an in-memory `CommerceAdapter`). `tests/contract/test_adapter_contract_prestashop.py`
additionally exercises the real `PrestaShopAdapter` against a live store, but skips
automatically unless `PRESTASHOP_BASE_URL`/`PRESTASHOP_API_KEY` point at one that's
reachable (see `docker/prestashop/README.md`).

```bash
cd chatbot/widget
npm test        # vitest smoke tests
npm run lint     # eslint
npm run build    # type-check + production bundle
```

## Current status

All four user stories (conversational discovery/navigation, add-to-cart-with-confirmation,
checkout-with-recap, and strategic promo suggestions) are implemented against `MockAdapter`
with full contract/unit/integration test coverage — see
[`specs/001-ai-shopping-assistant/tasks.md`](specs/001-ai-shopping-assistant/tasks.md) for
the authoritative, up-to-date task-by-task status. `PrestaShopAdapter` is implemented from
PrestaShop's official Webservice API docs but has not yet been integration-tested against a
live store in this environment — run the contract test suite above against a real
`docker compose up` stack before trusting it in production.

The multi-tenant backoffice (`backoffice/`, `tenancy-db/`, specs/002-backoffice-analytics)
has its core built through Phase 6 (tenancy, the analytics event pipeline, the admin API,
and the dashboard UI) — see that plan's "Progress so far" note for exactly what's real
versus deliberately deferred (a handful of dashboard pages, cost tracking, user invitations,
adapter connection testing).
