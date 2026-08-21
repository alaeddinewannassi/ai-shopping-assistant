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

## Technical Context

**Language/Version**: Python 3.11 (backend agent/adapter service); TypeScript (embeddable
chat widget, frontend)

**Primary Dependencies**: FastAPI + Pydantic (service/API layer), an LLM tool-calling
runtime for intent parsing and dialogue orchestration (model-agnostic via a thin function-
calling abstraction), httpx (PrestaShop Webservice REST client), redis-py (session/pending-
action state)

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
fully reproducible offline dev/test environment (no dependency on a live/production store);
secrets (store API keys) via environment/config only

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
│   │   │                  #   cart get/add/update/remove, validate_promo, apply_promo, checkout
│   │   ├── prestashop.py  # PrestaShop Webservice REST implementation
│   │   └── mock.py        # In-memory adapter for fast unit/scenario tests
│   ├── agent/             # Dialogue orchestration
│   │   ├── intents.py     # Natural-language intent parsing → structured actions
│   │   ├── pending.py     # Pending-action state machine (propose → confirm/decline → execute)
│   │   ├── recap.py       # Cart/checkout recap builder
│   │   └── dialogue.py    # Turn handling, ties intents + pending state + adapter + promo together
│   ├── promo/
│   │   ├── strategy.py    # Rule definitions (spend threshold, first-order, category-specific, stackability)
│   │   └── engine.py      # Evaluates cart/session against strategy rules → suggested code(s)
│   ├── session/
│   │   └── store.py       # Redis-backed Conversation Session (nav context, cart draft, pending action)
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
