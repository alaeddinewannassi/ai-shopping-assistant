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

```
backend/    FastAPI service: dialogue/agent logic, CommerceAdapter implementations, tests
widget/     Minimal embeddable chat widget (a single <assistant-chat-widget> custom element)
docker/     docker-compose.yml + PrestaShop reference-store fixture notes
specs/      Spec-kit artifacts: spec, plan, research, data model, contracts, tasks
```

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
cp backend/.env.example backend/.env   # fill in PRESTASHOP_API_KEY, LLM_API_KEY, etc.

# 3. Run the backend locally (or `docker compose up -d assistant-service`)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
uvicorn src.api.chat:app --reload

# 4. Build and try the widget
cd ../widget
npm install && npm run build
# open a static HTML page with:
#   <script src="dist/assistant-widget.js"></script>
#   <assistant-chat-widget api-base="http://localhost:8000"></assistant-chat-widget>
```

`LLM_PROVIDER=rule-based-stub` (the default for tests) needs no external API key at all and
runs a deterministic keyword matcher — good for exercising the full flow with zero cost or
network dependency before wiring up a real LLM.

## Running the tests

```bash
cd backend
source .venv/bin/activate
pytest tests/unit tests/contract tests/integration
```

`tests/unit/` and most of `tests/integration/` need no external services (they run against
`MockAdapter`, an in-memory `CommerceAdapter`). `tests/contract/test_adapter_contract_prestashop.py`
additionally exercises the real `PrestaShopAdapter` against a live store, but skips
automatically unless `PRESTASHOP_BASE_URL`/`PRESTASHOP_API_KEY` point at one that's
reachable (see `docker/prestashop/README.md`).

```bash
cd widget
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
