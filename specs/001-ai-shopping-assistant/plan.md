# Implementation Plan: AI Shopping Assistant for E-Commerce

**Branch**: `001-ai-shopping-assistant` | **Date**: 2026-08-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-ai-shopping-assistant/spec.md`

## Summary

Build a conversational shopping assistant that plugs into any e-commerce backend through a
single Commerce Adapter interface, with PrestaShop (run via Docker as the reference/dev
store) as the first concrete adapter implementation. The assistant lets a shopper browse and
navigate the catalog conversationally, add/edit cart items (always gated by an explicit
confirmation before mutation), review a full recap and give final confirmation before an
order is placed, and receive proactive, rule-based promo code suggestions that are always
validated by the underlying store before being reflected in totals. A lightweight, embeddable
chat widget provides the shopper-facing surface; a backend agent service owns dialogue state,
the adapter layer, the promo strategy engine, and structured audit logging.

**Project context**: This is an internship/academic deliverable, not a paid production
deployment — the plan intentionally favors **free-tier** options (LLM provider, hosting,
reference store) so it is realistic to build and demo without any billing setup, while
keeping the same interfaces/architecture a real production deployment would use.

## Technical Context

**Language/Version**: Python 3.11 (backend agent/adapter service); TypeScript (embeddable
chat widget, frontend)

**Primary Dependencies**: FastAPI + Pydantic (service/API layer), a swappable `LLMClient`
abstraction for intent parsing/dialogue orchestration selected via `LLM_PROVIDER` — default
`free-tier-hosted` (a free-tier tool-calling API such as Groq or Gemini's free tier — real
conversational quality, $0 cost within normal demo usage), with `rule-based-stub` (free,
deterministic, used for automated tests) as an alternative, and `hosted-paid` reserved for a
possible future production upgrade (see research.md §3a); httpx (PrestaShop Webservice REST
client), redis-py (session/pending-action state)


**Storage**: Redis for ephemeral conversation session state (navigation context, cart draft,
pending confirmation); PrestaShop's own MySQL database remains the sole source of truth for
catalog, cart, promo/cart-rules, and orders — this feature does not own a persistent
business-data store

**Testing**: pytest for unit + adapter contract tests; contract tests run against the
dockerized PrestaShop reference store in CI; scenario/integration tests drive full dialogue
flows (discovery → cart → recap → checkout → promo) through a Mock Adapter for speed and
the PrestaShop adapter for confidence; a small widget smoke test (headless browser) for the
frontend

**Target Platform**: Linux containers via Docker Compose (assistant-service, redis,
prestashop, mysql) for local dev/CI; assistant-service deployable as a standard container in
any Linux environment in front of a real store

**Project Type**: Web service (conversational backend) + embeddable web frontend widget

**Performance Goals**: Perceived response start < 2s p95 per conversational turn; adapter
calls to the underlying store < 500ms p95 under nominal load

**Constraints**: Zero silent mutations — every cart/promo/checkout mutation MUST pass
through an explicit pending-action confirmation gate, enforced in code and covered by tests;
the LLM's tool-calling schema MUST NOT include any mutation adapter method at all (read-only
+ propose-only tools only) — the confirmation gate is a structural capability boundary, not
a prompt instruction (research.md §9.3); fully reproducible offline dev/test environment (no
dependency on a live/production store); secrets (store API keys) via environment/config
only; when the store backend is unreachable, read-only browsing may degrade to a
clearly-labeled cached Catalog Snapshot but no mutation may ever be served from cache or
assumed to have succeeded (research.md §8, FR-016); free-text category/attribute terms MUST
be resolved against the store's real taxonomy via a deterministic resolver before being used
as a search filter — never asserted from LLM guesswork alone (research.md §9, FR-017)

**Scale/Scope**: MVP scope is a single storefront, single currency/locale, tens of
concurrent shopper sessions; multi-store/multi-currency explicitly out of scope (per spec
Assumptions)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|---|---|---|
| I. Conversational-First, Frictionless UX | Assistant must resolve discovery/navigation requests directly, target <2s perceived response start | PASS — dialogue/agent layer owns intent routing; performance goal captured in Technical Context |
| II. Platform-Agnostic Commerce Adapter | All store integration MUST go through one adapter interface; no platform code in core logic; dockerized reference store required | PASS — `adapters/` module defines the interface; `PrestaShopAdapter` and `MockAdapter` are the only implementations; `docker/` provides the reference PrestaShop stack |
| III. Explicit Confirmation Before Mutating Actions | Cart/promo/checkout mutations require recap + explicit confirm; navigation is read-only | PASS — `agent/` implements a pending-action state machine; no adapter mutation call is reachable without a confirmed pending action (enforced + tested) |
| IV. Test-First & Adapter Contract Testing | Every adapter method has a contract test against the Docker store; dialogue behaviors have scenario tests before implementation | PASS — `tests/contract/` (per adapter method) and `tests/integration/` (per user story) are defined in Project Structure below and scheduled before implementation tasks |
| V. Observability & Auditability | Every navigation/cart/promo/checkout action logged with intent, action, outcome | PASS — `logging/` module provides structured JSON audit logging invoked by the agent layer for every action |
| VI. Transparent, Rule-Based Promotion Strategy | Promo suggestions are deterministic/rule-based and always store-validated; no fabricated discounts | PASS — `promo/` module implements an explicit rule engine; discounts are only ever reflected after `adapter.validate_promo()`/`apply_promo()` confirms |

No violations requiring justification — Complexity Tracking table is empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-ai-shopping-assistant/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   ├── commerce-adapter.md
│   └── promo-strategy.md
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
backend/
├── src/
│   ├── adapters/          # Commerce Adapter interface (Protocol/ABC) + PrestaShopAdapter + MockAdapter
│   │   ├── base.py        # CommerceAdapter interface: search_products, get_product,
│   │   │                  #   list_categories, list_attributes, cart get/add/update/remove,
│   │   │                  #   validate_promo, apply_promo, checkout
│   │   ├── resilience.py  # Circuit breaker + AdapterUnavailableError wrapper around adapter calls
│   │   ├── prestashop.py  # PrestaShop Webservice REST implementation
│   │   └── mock.py        # In-memory adapter for fast unit/scenario tests
│   ├── agent/             # Dialogue orchestration
│   │   ├── llm_client.py  # Swappable LLM provider abstraction (free-tier-hosted/local/rule-based-stub via LLM_PROVIDER)
│   │   ├── intents.py     # Natural-language intent parsing → structured actions (uses llm_client);
│   │   │                  #   tool schema exposed to the LLM is read-only + propose_action ONLY —
│   │   │                  #   no mutation adapter method is ever LLM-callable (research.md §9.3)
│   │   ├── taxonomy_resolver.py  # Deterministic term→real-taxonomy resolver (exact/ambiguous/
│   │   │                  #   unsupported/stale) backing FR-017; never LLM/embedding-based (research.md §9.1)
│   │   ├── pending.py     # Pending-action state machine (propose → confirm/decline → execute);
│   │   │                  #   confirm_action() is the ONLY code path that may call a mutation
│   │   │                  #   adapter method, and is not itself an LLM-callable tool (research.md §9.3)
│   │   ├── recap.py       # Cart/checkout recap builder
│   │   └── dialogue.py    # Turn handling, ties intents + pending state + adapter + promo together
│   ├── promo/
│   │   ├── strategy.py    # Rule definitions (spend threshold, first-order, category-specific, stackability)
│   │   └── engine.py      # Evaluates cart/session against strategy rules → suggested code(s)
│   ├── session/
│   │   ├── store.py       # Redis-backed Conversation Session (nav context, cart draft, pending action)
│   │   ├── catalog_cache.py  # Redis-backed CatalogSnapshot: read-only fallback cache for discovery/navigation
│   │   │                  #   when the adapter is unreachable (research.md §8); never used for cart/promo/checkout
│   │   └── taxonomy_cache.py  # Redis-backed TaxonomySnapshot: real category/attribute vocabulary cache
│   │                      #   used by taxonomy_resolver.py (research.md §9.1-9.2); distinct purpose from
│   │                      #   catalog_cache.py (that one is outage-only; this one is always-on grounding)
│   ├── logging/
│   │   └── audit.py       # Structured JSON audit logging for every navigation/cart/promo/checkout action
│   └── api/
│       └── chat.py        # FastAPI endpoint(s): POST /chat, GET /health
└── tests/
    ├── contract/          # One test module per CommerceAdapter method, run against dockerized PrestaShop
    ├── integration/        # End-to-end scenario tests, one file per user story (US1-US4)
    └── unit/              # promo engine, pending-action state machine, recap builder unit tests

widget/
├── src/                   # Minimal embeddable chat widget (TypeScript) for demoing inside a PrestaShop theme
└── tests/                 # Widget smoke test (renders, sends/receives a chat turn)

docker/
├── docker-compose.yml     # prestashop + mysql + redis + assistant-service (reference environment)
└── prestashop/            # Fixture/config scripts: demo catalog, demo promo/cart rules for testing
```

**Structure Decision**: A backend agent/adapter service (`backend/`) is the core of this
feature and where all constitution-mandated seams live (adapter interface, confirmation gate,
promo engine, audit log). A minimal `widget/` provides the shopper-facing chat surface so the
assistant can be demoed embedded in a real storefront. `docker/` hosts the containerized
PrestaShop reference environment required by Principle II, shared by contract tests and local
development.

## Complexity Tracking

> No Constitution Check violations — table intentionally left empty.
