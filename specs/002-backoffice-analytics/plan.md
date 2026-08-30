# Implementation Plan: Multi-Tenant Backoffice & Analytics

**Feature**: `002-backoffice-analytics`
**Depends on**: `001-ai-shopping-assistant` (shipped)
**Status**: Implemented (core, all 7 phases) directly from this plan — never run through
`/speckit-specify` → `/speckit-plan` → `/speckit-tasks`, so there's no separate spec.md/
tasks.md the way `001-ai-shopping-assistant` has; this plan doc is the authoritative record
of what was built and what was deliberately deferred.

**Progress so far**: Phase 1 (T101-T107) done. Phase 2 core done — T201-T204 (tenant
resolution, `TenantRuntime` pool replacing the import-time singletons, per-tenant Redis key
namespacing), tested by `chatbot/backend/tests/integration/test_multitenancy.py` (T208-style)
with no regressions to the pre-002 suite (T209-style). T205 (per-tenant promo rules) landed
as part of the runtime wiring; T206-T207 (full origin enforcement, rate limiting, widget
changes) not yet done. D6 was revised mid-build (see below) into three top-level projects —
`chatbot/`, `backoffice/`, `tenancy-db/` — instead of one shared FastAPI app; `backoffice/backend/`
currently only has its T501 skeleton (FastAPI app + DB-backed `/health`).

Phase 3 (event pipeline) core done — T301-T304, T307, T309, T310, T311. Three items
deliberately scoped down rather than faked: T305 (per-call adapter/LLM timing) and T306
(token/cost) aren't built because nothing in this codebase produces real values for them
yet (`FreeTierHostedLLMClient.parse_turn` is itself unimplemented); T308 (PII redactor)
isn't built because no call site logs raw message text today, so there's nothing to redact.
See Phase 3's task list below for the full account of what each of those three actually
needs before it's real.

Phase 4 (aggregation & query layer) partially done — T403/T404/T406: `get_overview()` +
`get_funnel()` in `backoffice/backend/src/analytics/queries.py`, reading raw events
directly (no rollup tables yet). T401/T402/T405 (rollup schema, the scheduler process,
retention enforcement) explicitly deferred — building an always-running scheduler with
nothing yet to refresh isn't a real feature, and raw-event queries are already correct,
just not optimized for huge date ranges (D5's anticipated order: correctness before that
optimization).

Phase 5 (admin API) core done — T501-T507, T509, T511. Auth (argon2 + JWT cookies), RBAC
(`require_tenant_role` dependency factory, superadmin bypass), tenant CRUD, adapter/LLM
config (encrypted at rest, masked in every response — tested that the plaintext secret
never appears in any response body), widget key issue/revoke, promo rule editor, and
`admin_audit` on every mutation. T504's OpenAPI contract was written first and verified
afterward to match the implementation's own generated schema exactly (16/16 paths).
Deferred, each for a stated reason (see Phase 5's task list): T502's optional TOTP/MFA and
refresh-token revocation; T505's timeseries/breakdowns/cost (blocked on Phase 4's deferred
rollups and Phase 3's absent cost data); T507's adapter "Test connection" (a genuine open
architecture question — needs either a shared adapter package with `chatbot/backend` or an
internal health-check proxy, since the two backends deliberately share no code beyond
`tenancy-db`); T508 (user/membership invitations); T510 (CSV/JSON export).

Phase 6 (backoffice UI) core done — T601-T604, T607, T609-T611. Auth flow, tenant
switcher, app shell, Overview + Funnel + Sessions/session-detail + Settings (adapter/LLM
config, widget keys, promo rules) pages, superadmin tenant list. No Tailwind/Recharts (a
deliberate simplification, and for the funnel specifically the *correct* choice per the
dataviz skill's own anti-pattern rule against double-encoding a single series). Not built:
Confirmations/Commerce/Promos/Quality/Cost pages, privacy/retention and user-management
settings — each traces back to a Phase 3/4/5 gap already recorded above (no order-total
capture, no cost data, no retention enforcement, no user-invite endpoints). The actual
login → overview → funnel → sessions → session-detail → settings flow (including live
writes: issuing a widget key, saving a promo rule) was verified end-to-end with both dev
servers running against a seeded database, driven by Playwright with screenshots at each
step — not simulated, and not left behind as a committed test suite.

Phase 7 (ops & hardening) done except the scheduler, which still doesn't exist because
there's still nothing to schedule (T401/T402 remain deferred). Docker images for all three
new/changed services were actually built and, for the frontend, run and smoke-tested — not
just compose-config-validated. The bootstrap script (T702) migrates an existing
single-tenant deployment's adapter/LLM config and, explicitly, warns rather than silently
drops promo rules if you skip `--promo-rules-json`. The write path was load-tested with real
numbers (~19µs/call enqueue cost, ~52,000 events/s sustained, zero drops — both far past
their stated targets). The security review's one real finding — `TenantConfig` would have
printed decrypted API keys in its default `repr()` — is fixed and tested.

167 tests passing across all four projects (121 chatbot/backend + 12 tenancy-db + 27
backoffice/backend + 7 backoffice/frontend), zero regressions.

**All 7 phases of this plan have a "core done" pass.** What remains, per-phase, is recorded
in each phase's task list above rather than repeated here — the throughline is: no rollup
tables or scheduler (Phase 4), no real LLM integration to measure cost against (Phase 3),
no adapter "Test connection" (an open cross-service architecture question, Phase 5/7), and
several dashboard pages/settings that trace back to those same gaps (Phase 6).

## 1. Goal

Give merchants and operators a web backoffice that answers, per store:

- **Usage** — how many shoppers/sessions/turns, when, for how long, on which pages.
- **Effectiveness** — how often the assistant turns a conversation into a cart and an order,
  and where the funnel leaks.
- **Quality** — intent-parse failures, zero-result searches, latency against the
  Constitution's <2s target, adapter/LLM error rates, token spend.
- **Auditability** — replay any single conversation's decision trail (Principle V), already
  half-built as `GET /audit/{session_id}`.

…with **multi-tenancy** so one deployment serves many stores, each with its own credentials,
promo rules, widget key, data isolation, and users.

## 2. Where we are today (gap analysis)

| Concern | Today | Gap |
|---|---|---|
| Tenancy | None. `chatbot/backend/src/api/chat.py:80-83` (pre-refactor location) builds **one** module-level `_adapter`/`_session_store`/`_dialogue_ctx` from process env vars at import time | Everything is a process-wide singleton; store credentials live in `.env` |
| Analytics store | None. `chatbot/backend/src/logging/audit.py` writes a JSON line to stdout + a Redis list with a **1-hour TTL** | Data is gone before any dashboard could read it; no aggregation, no SQL |
| Event richness | `{timestamp, session_id, intent, action, outcome, details}` | No tenant, no turn id, **no latency**, no token/cost, no amounts, no product ids, no error class, no schema version. `timestamp` is a bare float |
| Turn instrumentation | `handle_turn()` (`chatbot/backend/src/agent/dialogue.py:39+`) calls `log_action()` ad hoc at ~25 sites | No turn boundary, no timing, no per-call adapter/LLM spans |
| Sessions | `SessionStore` Redis keys `session:{id}`, 1h TTL, in-memory fallback (`chatbot/backend/src/session/store.py`) | Key namespace has no tenant prefix — two tenants would collide |
| Auth | None on any endpoint; CORS `allow_origins=["*"]` | Admin surface needs real auth; widget origin needs per-tenant allowlisting |
| Widget | `<assistant-chat-widget api-base=…>`, session id in `localStorage` (`chatbot/widget/src/widget.ts:97-101`) | No tenant key, no client-side metadata (page, widget version) |
| Promo rules | Global `load_rules()` (`chatbot/backend/src/promo/strategy.py`) | Must become per-tenant |

**The "enhance the last commit's strategy" instinct is right**: `GET /audit/{session_id}` is a
good *debugging* surface but a bad *analytics* substrate — ephemeral, unindexed, per-session
only, and missing every numeric dimension a dashboard needs. Phase 3 below replaces the
storage and enriches the schema **without changing the audit endpoint's contract**.

## 3. Architecture decisions

### D1 — PostgreSQL as the analytics store (Redis stays for hot session state)

Add a `postgres` service; keep Redis for `SessionStore`/`PendingAction`/caches. Postgres gets
SQLAlchemy 2.0 + Alembic migrations.

*Why not:* MySQL (would couple assistant data to PrestaShop's own DB — violates Principle II's
spirit); ClickHouse/Timescale (right at 10⁸ events, overkill at this stage — plain partitioned
Postgres + rollup tables carries this comfortably and the query layer keeps the door open);
Redis-only with long TTL (no aggregation, no joins, no retention control).

### D2 — Tenant resolved from a public widget key, not from the URL path

Widget sends `X-Assistant-Key: pk_live_…` (a **public**, origin-restricted key). A FastAPI
dependency resolves it to a `Tenant` and puts it in a request-scoped context. Keep the
existing `/chat` shape; when no key is present, fall back to `DEFAULT_TENANT_SLUG` so today's
single-tenant deployments and the whole existing test suite keep passing.

*Why not* a path prefix (`/t/{slug}/chat`): breaks the widget contract and every existing test
for no isolation benefit. *Why not* a secret key in the widget: it ships in browser JS — it
cannot be secret. Abuse control comes from origin allowlist + per-tenant rate limits.

### D3 — Adapter pool replaces the import-time singleton

`TenantRuntime` (adapter + LLM client + promo rules + resolvers) built lazily per tenant and
cached with TTL, invalidated on config change. This preserves the per-instance circuit breaker
in `adapters/resilience.py` (each tenant gets its own breaker — one broken store must not trip
another's). Store credentials move from `.env` to `tenant_adapter_config`, encrypted at rest
(Fernet via `APP_ENCRYPTION_KEY`, envelope/KMS later), decrypted only inside the factory,
never logged, never returned by the admin API (write-only fields + masked display).

### D4 — Event write path must never slow a chat turn

`handle_turn()` is **synchronous** (runs in FastAPI's threadpool), so: a bounded
`queue.Queue` + a daemon writer thread doing batched `executemany` inserts every ~250 ms or
500 events. Queue full → drop + increment a counter (never block, never raise). The stdout
JSON line stays unconditional, so Principle V holds even if Postgres is down. A Redis Stream
buffer is the documented upgrade path if at-least-once delivery is later required.

### D5 — Read path: rollups for ranges, raw events for drill-down

`analytics_daily` / `analytics_hourly` aggregate tables refreshed incrementally (last 2 hours
every 5 min, full previous day nightly). Dashboards over >24h read rollups; session explorer
and the last 24h read raw events. Rollup jobs run in a small scheduler process (APScheduler in
a dedicated container) so they don't multiply across uvicorn workers.

### D6 — Backoffice is a wholly separate project, not a router in the chatbot's app (REVISED)

Originally scoped as an `src/admin/` router mounted into the chatbot's FastAPI app. Revised:
the backoffice is its own top-level project, `backoffice/`, with its own backend
(`backoffice/backend/` — separate FastAPI process, own deploy, own auth/CORS boundary) and
its own frontend (`backoffice/frontend/` — React 18 + TypeScript + Vite, same toolchain
family as `chatbot/widget/`, TanStack Query + Recharts + Tailwind). The chatbot lives in its
own top-level project too: `chatbot/backend/` + `chatbot/widget/`.

The two backend services share **no process and no code import** — an outage, dependency
bump, or security incident in one must never touch the other, and "operator dashboard" vs.
"shopper-facing chat" is a real trust boundary, not just a router prefix. The only thing they
share is data: `tenancy-db/`, a third top-level package (SQLAlchemy models + repositories +
Alembic migrations for every tenancy/admin/analytics table in §4-§5). `chatbot/backend/`
depends on it read-only (resolving tenant config); `backoffice/backend/` depends on it
read-write (all CRUD). Both install it as a local editable package
(`pip install -e ../../tenancy-db`) — see `README.md`'s repository-layout section.

Cardinality: **one backoffice deployment administers many tenants**, and each tenant's
`chatbot/backend/` traffic can itself come from many storefronts (one `X-Assistant-Key` per
site) hitting one running chatbot service — resolved per-request by
`chatbot/backend/src/tenancy/resolver.py` (T202), not by running a separate chatbot process
per tenant.

## 4. Data model (new tables)

**Tenancy & access**
- `tenant` — id, slug, name, status(`active|suspended`), plan, timezone, settings JSONB, timestamps
- `tenant_adapter_config` — tenant_id, platform(`prestashop|mock|…`), base_url, host_header, lang_id, `api_key_encrypted`, checkout defaults (customer/address/carrier/currency/order-state/payment module), is_active
- `tenant_llm_config` — tenant_id, provider, model, `api_key_encrypted`, monthly token budget, budget action(`warn|degrade_to_stub|block`)
- `tenant_promo_rule` — per-tenant version of `promo/strategy.py` rules (code, condition, priority, stackable, active)
- `widget_key` — tenant_id, `public_key`, allowed_origins[], is_active, last_used_at, revoked_at
- `admin_user` — email, password_hash (argon2), name, is_superadmin, mfa_secret, status
- `tenant_membership` — admin_user_id, tenant_id, role(`owner|admin|analyst|support`)
- `admin_audit` — who did what to which tenant's config, when, from where (the backoffice audits *itself*)

**Analytics**
- `conversation_session` (one row per session, upserted) — tenant_id, session_id, visitor_hash, started_at, last_seen_at, turn_count, first_page_url_path, locale, device_type, country, outcome(`browsing|cart|ordered|abandoned`), cart_id, order_id, order_total_minor, currency
- `assistant_event` (append-only, monthly-partitioned on `occurred_at`) — see §5
- `analytics_hourly` / `analytics_daily` — tenant_id, bucket, dimension key set, counters/sums
- `event_ingest_stats` — dropped counts, flush latency, writer health

**Indexes**: `(tenant_id, occurred_at DESC)`, `(tenant_id, session_id, occurred_at)`,
`(tenant_id, event_type, occurred_at)`, GIN on `details`.
**Isolation**: every repository method takes `tenant_id` as a required first argument; Postgres
Row-Level Security policies on all tenant-scoped tables as defense in depth.

## 5. Event taxonomy — the enriched replacement for `log_action`

Existing signature stays source-compatible; the record grows:

```
event_id          uuid
tenant_id         uuid            # NEW
session_id        text
turn_id           uuid            # NEW — groups every event of one conversational turn
seq               int             # NEW — order within the turn
occurred_at       timestamptz     # was a bare float
schema_version    int             # NEW
event_type        enum            # NEW, see below
intent            text            # existing
action            text            # existing
outcome           text            # existing
duration_ms       int             # NEW
llm_provider / llm_model / prompt_tokens / completion_tokens / cost_micros   # NEW
adapter_calls     int             # NEW
adapter_ms        int             # NEW
cart_id / order_id / product_id / variant_id / quantity                      # NEW
amount_minor / discount_minor / currency / promo_code                        # NEW
error_class / error_message                                                  # NEW
page_url_path / widget_version / device_type / locale                        # NEW (client hints)
details           jsonb           # existing tail
```

`event_type` values: `turn_started`, `intent_parsed`, `intent_unresolved`, `search_performed`,
`zero_results`, `navigation_changed`, `product_shown`, `action_proposed`, `action_confirmed`,
`action_declined`, `action_expired`, `cart_mutated`, `promo_suggested`, `promo_applied`,
`promo_rejected`, `checkout_proposed`, `order_placed`, `adapter_unavailable`,
`circuit_breaker_open`, `llm_error`, `turn_completed`.

**Instrumentation**: a `TurnContext` created in `handle_turn()` carries
`(tenant_id, session_id, turn_id, seq, timers)` and is threaded through
`intents.py` / `pending.py` / `promo/engine.py`. Adapter and LLM calls are wrapped in timing
decorators so `adapter_ms` / `llm_ms` are measured, not guessed. `turn_completed` carries the
end-to-end latency that feeds the Principle I SLO panel.

**Privacy**: raw shopper message text is **off by default**, opt-in per tenant, retained 30
days, and passed through an email/phone/card-number redactor before storage. Visitor identity
is a salted hash; no raw IP, no payment data ever (Constitution, Tech Constraints).

## 6. Dashboard surface

| Page | Contents |
|---|---|
| **Overview** | Sessions, turns, unique visitors, assisted GMV, conversion rate, p95 latency, error rate — each with sparkline + period-over-period delta |
| **Funnel** | sessions → discovery → proposal → confirmation → cart mutation → checkout proposal → order, with drop-off % per step |
| **Confirmations** | Propose→confirm rate, decline rate, expired/stale proposals, by action type — the direct supervision view for Principle III |
| **Commerce** | AOV, items/order, top products *surfaced* vs *added* vs *purchased*, category mix, revenue by day |
| **Promos** | Suggestion→acceptance rate per code, discount cost, incremental AOV, adapter rejection rate |
| **Quality** | Unresolved-intent rate, zero-result searches (with the actual queries — the best catalog-gap signal), latency histogram vs 2s target, adapter/LLM error breakdown, circuit-breaker timeline |
| **Conversations** | Searchable session list → session detail replaying the full audit trail (an upgrade of `GET /audit/{session_id}`) |
| **Cost** | Tokens and estimated spend per tenant/day/model, budget burn-down |
| **Settings** | Adapter config + "Test connection", widget key + embed snippet + origin allowlist, promo rules editor, LLM config, retention/privacy toggles, users & roles |
| **Superadmin** | Cross-tenant table: health, volume, error rate, spend; impersonate-for-support (audited) |

## 7. Task breakdown

Format follows `specs/001-*/tasks.md`: `[ID] [P?] Description`. `[P]` = parallelizable.

### Phase 1 — Persistence foundation
- T101 Add `postgres:16` to `docker/docker-compose.yml`; `DATABASE_URL` in `.env.example`
- T102 Add SQLAlchemy 2.0 + Alembic + psycopg + argon2 + cryptography to `pyproject.toml`
- T103 Create the shared data layer (engine, session factory, base, health check) — done, lives in `tenancy-db/src/tenancy_db/` per D6's revision, not `backend/src/db/`
- T104 Alembic baseline migration: tenancy tables (§4)
- T105 [P] `tenancy-db/src/tenancy_db/crypto.py` — Fernet encrypt/decrypt for stored credentials, keyed by `APP_ENCRYPTION_KEY`
- T106 [P] Repository layer with mandatory `tenant_id` argument + Postgres RLS policies
- T107 Test: tenant isolation — tenant A's repo calls can never read tenant B's rows (**blocking, security-critical**)

### Phase 2 — Multi-tenancy core
- T201 `Tenant` domain model + loader with in-process TTL cache
- T202 `X-Assistant-Key` resolution dependency + `DEFAULT_TENANT_SLUG` fallback for single-tenant/test mode
- T203 Replace the module-level singletons in `src/api/chat.py:80-100` with a `TenantRuntime` pool (adapter, LLM client, promo rules, resolvers, per-tenant circuit breaker)
- T204 Namespace Redis keys: `session:{id}` → `t:{tenant}:session:{id}`; same for audit/catalog/taxonomy caches
- T205 Per-tenant promo rules: `load_rules()` reads `tenant_promo_rule`, falls back to the packaged defaults
- T206 Per-tenant CORS/origin enforcement + per-tenant rate limiting (Redis token bucket)
- T207 [P] Widget: `tenant-key` attribute, sends `X-Assistant-Key` + page path + widget version
- T208 Test: two tenants configured against different mock stores in one process never cross-contaminate sessions, carts, or breakers
- T209 Test: existing US1–US4 integration suites pass unchanged in default-tenant mode (**no regressions**)

### Phase 3 — Event pipeline (the audit-strategy upgrade) — core done, 3 items scoped down
- T301 `assistant_event` + `conversation_session` migration — done, **not monthly-partitioned**
  (a plain indexed table; real Postgres partitioning is DDL work with no cross-dialect
  SQLAlchemy declarative story and no correctness need yet — documented follow-up in
  `tenancy_db/models/analytics.py`'s docstring, not silently skipped)
- T302 `TurnContext` (tenant/session/turn/seq/timers) — done
  (`chatbot/backend/src/agent/turn_context.py`, a `contextvars.ContextVar` set for the
  duration of one `handle_turn()` call)
- T303 Rewrite `logging/audit.py` — done, but simpler than scoped: `log_action()`'s
  signature and stdout line are **byte-for-byte unchanged** (no separate `log_event()`);
  enrichment happens by reading the active `TurnContext`, so all ~25 existing call sites in
  `dialogue.py` needed zero edits (see T307)
- T304 Bounded-queue + daemon-thread batch writer, drop-with-counter on overflow — done
  (`_event_queue`/`_writer_loop` in `logging/audit.py`; `dropped_event_count()` exposes the
  counter). No graceful-shutdown drain — the daemon thread is killed with the process, which
  can lose an in-flight batch (≤500 events) on shutdown; acceptable today, a real fix needs
  an ASGI shutdown hook to call `wait_for_drain()`
- T305 Timing wrappers on every `CommerceAdapter`/`LLMClient` call — **not done**. Only
  turn-level latency exists (`turn_elapsed_ms`, real and tested) — no per-call `adapter_ms`/
  `llm_ms` breakdown. Needs a wrapper around `CommerceAdapter`'s methods and `LLMClient.parse_turn`
- T306 Token + cost capture — **not done, nothing to instrument yet**:
  `FreeTierHostedLLMClient.parse_turn` itself raises `NotImplementedError` (never wired to a
  real API, `llm_client.py`'s own docstring flags this as deferred from feature 001) and
  `RuleBasedStubClient` has no tokens. Revisit once a real LLM call exists
- T307 Instrument the ~25 existing `log_action` sites — **done without touching any of
  them**: T303's contextvar design means enrichment (`tenant_id`/`turn_id`/`seq`) is
  automatic. What's genuinely new content per event: none of the numeric fields from T305/
  T306 (not built) — only what T301's leaner schema actually has
- T308 [P] PII redactor + per-tenant message-capture opt-in — **not done, nothing to
  redact yet**: no call site logs raw shopper message text today (checked directly), so
  there's no content a redactor would run against. Build this alongside whichever future
  work first adds raw-text capture, not before
- T309 [P] `conversation_session` upsert — done, **partial outcome classification**:
  tracks `browsing -> ordered` only (`ConversationSession.has_completed_order`, a real,
  already-existing signal). Does **not** detect `"cart"` (has items, not yet checked out) —
  that needs an adapter `get_cart()` call this function deliberately doesn't make, since a
  chat turn must never pay analytics-classification latency (D4). `"abandoned"` is
  inherently a time-based batch classification (Phase 4's scheduler, not a single turn)
- T310 Rewire `GET /audit/{session_id}` to read Postgres, Redis as fallback — done,
  contract unchanged; the endpoint is now tenant-resolved via `Depends(resolve_tenant_runtime)`
- T311 Test: a full US2 turn emits the expected event sequence with non-null latency;
  Postgres down → chat still succeeds and stdout still logs — done
  (`chatbot/backend/tests/integration/test_analytics_pipeline.py`)

### Phase 4 — Aggregation & query layer — raw-event path done, rollup/scheduler deferred
- T401 `analytics_hourly`/`analytics_daily` schema + incremental refresh job — **not built**.
  Deferred along with T402 (no point building a rollup table with nothing to refresh it) —
  see below
- T402 Scheduler container (APScheduler): rollups, retention purge, partition maintenance —
  **not built**. This is a real always-running process (its own entrypoint, Dockerfile,
  compose service, new dependency) — standing one up with nothing yet to schedule would be
  unbuilt infrastructure pretending to be a feature. Revisit once T401 rollups exist or a
  tenant's raw-event volume actually makes >24h-range queries slow (D5 anticipated this
  order: raw-event correctness first, rollup optimization once needed)
- T403 `backoffice/backend/src/analytics/queries.py` — done, **raw-event only** (no
  rollup-or-raw routing yet, since there's no rollup to route to) — `get_overview()` +
  `get_funnel()`, both scan `assistant_event`/`conversation_session` directly for `[start,
  end)`. Always correct, not yet optimized for huge date ranges
- T404 Funnel/conversion computation — done. Stages: sessions → discovery → proposal →
  confirmed → cart_mutated → checkout_proposed → ordered, each a distinct-session count.
  Needed one small upstream fix: the "confirm success" `log_action` call in
  `_handle_confirm()` didn't previously record *which* action type was confirmed (cart
  mutation vs. checkout look identical otherwise) — added `details={"action_type":
  pending.action_type}`, mirroring the decline path's existing `declared_action_type` field
- T405 [P] Retention enforcement per tenant — **not built**, tied to T402's scheduler
- T406 Test: synthetic event fixtures → asserted funnel/overview numbers — done
  (`backoffice/backend/tests/test_analytics_queries.py`; every expected count/latency value
  is hand-computed from a fully-known fixture, not just "did it run")

### Phase 5 — Admin API (`backoffice/backend/`) — core done, 3 items deferred
- T501 `backoffice/backend/src/api/main.py` FastAPI app, strict CORS via `ADMIN_CORS_ORIGINS`,
  `allow_credentials=True` (cookie auth needs it) — done
- T502 Auth — done, **without TOTP/MFA** (the optional part; not built). Argon2 passwords
  (`src/auth/passwords.py`), JWT access (15 min) + refresh (30 days) in httpOnly/secure/
  samesite=strict cookies (`src/auth/tokens.py`). Real gap, not silently accepted: no
  server-side refresh-token revocation list yet — a leaked refresh token is valid until it
  expires, no "log out everywhere"
- T503 RBAC dependency — done (`src/auth/dependencies.py`): `require_tenant_role(*roles)`
  is a dependency *factory* checked against the `tenant_id` path parameter via
  `TenantMembershipRepository.get_role()`, bypassed for `is_superadmin`. A membership on one
  tenant grants nothing on another (tested explicitly)
- T504 OpenAPI contract — done, written **before** any endpoint code
  (`specs/002-backoffice-analytics/contracts/admin-api.yaml`), and verified afterward to
  match exactly: all 16 contracted paths present in the running app's own OpenAPI schema,
  nothing extra except `/health` (deliberately outside the contract)
- T505 Analytics endpoints — done for overview + funnel (wired to Phase 4's query layer);
  **no timeseries/breakdowns/cost params** — those need the rollup tables Phase 4 deferred,
  or real LLM cost data Phase 3 doesn't have
- T506 Sessions list + session detail (audit replay) — done
- T507 Tenant CRUD, adapter/LLM config, widget key issue/revoke, promo rule editor — done,
  **without "Test connection"**. This surfaced a real architecture question, not a shortcut:
  checking real store connectivity means calling the configured adapter, and
  `CommerceAdapter`/`PrestaShopAdapter` code lives only in `chatbot/backend`, which this
  service deliberately never imports (D6). Needs either a shared adapter package (mirroring
  `tenancy-db`) or an internal-only health-check proxy between the two services — an open
  decision, documented in `src/api/routes/tenants.py`'s module docstring, not built either way
- T508 User/membership management + invitations — **not built** (admin users/memberships
  can only be seeded directly against the database for now, e.g. via a script — no HTTP
  surface to invite/add one yet)
- T509 `admin_audit` write on every mutating admin action — done, tested per mutation type
  (tenant create/update, adapter/LLM config upsert, widget key issue/revoke, promo rule upsert)
- T510 [P] CSV/JSON export endpoints — **not built**
- T511 Authz matrix test — done, but scoped as *one representative endpoint per distinct
  RBAC dependency* (5 dependencies × 6 roles, including a same-user-different-tenant negative
  case and an unauthenticated-must-be-401-not-403 check) rather than literally every one of
  the 16 endpoints × every role — a regression in any of the 5 dependency functions is caught
  regardless of which endpoint uses it. Not full per-endpoint contract tests either (e.g. no
  request/response schema validation against the OpenAPI file itself)

### Phase 6 — Backoffice UI (`backoffice/frontend/`) — core done, deviates from plan in 2 ways
- T601 Scaffold `backoffice/frontend/` — done, **Vite + React + TS + TanStack Query**, but
  **no Tailwind, no Recharts**. Tailwind was dropped as a deliberate simplification (a
  small internal tool doesn't need a utility-CSS build layer; plain CSS custom properties
  carry the dataviz skill's design tokens directly, light/dark aware). Recharts was never
  needed: the dataviz skill's own anti-patterns guidance says a single-series bar chart
  (the funnel, the only chart built) wants ONE color with bar length as the only magnitude
  encoding — a hand-rolled flexbox bar is the correct-by-the-skill's-own-rules choice, not
  a corner cut to avoid a dependency
- T602 Auth flow (login/logout via `src/lib/auth.tsx`'s `AuthProvider`), tenant switcher
  (`AppShell`, shown when a user has >1 membership), app shell with nav — done
- T603 Overview page — done, **stat tiles only, no sparklines/deltas** (no rollup history
  to compute a delta against — same Phase 4 gap this always traces back to)
- T604 Funnel page — done. **Confirmations** (propose→confirm/decline rates by action type)
  — **not built**; the raw event stream has the data, just no dedicated query/page yet
- T605 Commerce + Promos pages — **not built**. Promo rule CRUD exists (folded into
  Settings, not a standalone page); no Commerce page at all — needs order-total capture on
  `ConversationSessionRecord`, which Phase 3 didn't add (see its "not done" T309 note)
- T606 Quality/latency/errors page — **not built as a standalone page**; its numbers
  (latency, error rate) are already on the Overview page, just not broken out with
  breakdowns-by-dimension
- T607 Conversations explorer + session replay timeline — done (`Sessions.tsx` +
  `SessionDetail.tsx`)
- T608 Cost page — **not built** (no real LLM cost data exists yet — see Phase 3's T306)
- T609 Settings — done: adapter config, LLM config, widget key issue/revoke + embed
  snippet, promo rule editor. **Not built**: privacy/retention toggles (nothing to
  configure — no retention enforcement exists, Phase 4's T405), user management (Phase 5's
  T508 wasn't built either)
- T610 Superadmin cross-tenant view — done, but minimal: tenant list + create only
  (`AdminTenants.tsx`) — no health/volume/error-rate/spend columns (spend needs T608's cost
  data; volume/error-rate would need a per-tenant analytics query run N times, not built)
- T611 Tests — done for Vitest (7 tests: `StatTile`, `FunnelChart` — including a test that
  explicitly asserts the single-color/length-only-encoding decision — and `Login`, mocking
  the API layer). **The actual login → overview → session detail → funnel → settings flow
  was verified live**, not simulated: both dev servers launched against a seeded SQLite
  database, driven end-to-end with Playwright (screenshots taken at each step, zero console
  errors beyond expected 401s/404s for not-yet-authenticated/not-yet-configured states), then
  torn down — no lasting Playwright test suite was committed (a one-off manual verification,
  not T611's "smoke path" as an automated CI-facing test)

### Phase 7 — Ops & hardening — done except the scheduler (which still doesn't exist)
- T701 Compose wiring — done for postgres, `backoffice-service`, `backoffice-frontend`
  (nginx serving the built SPA, with the client-side-routing fallback that needs).
  **No scheduler service** — there's still nothing to schedule (T401/T402 deferred in
  Phase 4). All three new/changed Dockerfiles were actually built with `docker build`
  (not just `docker compose config`-validated) — chatbot/backend's for the first time ever
  in this project — and the frontend image was run and smoke-tested (root path and a deep
  client-side route both 200)
- T702 Bootstrap script — done: `backoffice/backend/scripts/bootstrap.py`, idempotent,
  migrates adapter/LLM config from the same env vars `chatbot/backend`'s
  `legacy_env_tenant_config()` reads. Surfaces the promo-rules gap explicitly (a
  `--promo-rules-json` flag + a printed warning if omitted) rather than silently losing a
  migrated deployment's promo suggestions — documented in `backoffice/README.md`'s
  migration section, verified by running the exact documented command
- T703 Ingest-pipeline health — done, narrowly: `dropped_event_count()` (the analytics
  writer's overflow counter) is now in `chatbot/backend`'s own `/health` response. **Not
  aggregated into a backoffice dashboard page** — that needs the same cross-service
  architecture decision as T507's "Test connection" (backoffice/backend has no path to
  chatbot/backend's `/health`, by design)
- T704 Load test — done, with real measured numbers, not just a passing assertion: enqueue
  cost ~19µs/call (~250x under the 5ms target), sustained ~52,000 events/s locally with
  zero drops (~500x over the 100/s target). Local SQLite, not the Postgres this targets in
  production — directionally solid given the enqueue path never touches the database at all,
  but not a substitute for a real Postgres benchmark under production-like concurrency
- T705 Security review — done, one real finding fixed: `TenantConfig` (chatbot/backend)
  held decrypted adapter/LLM API keys as plain dataclass fields, so its default `__repr__`
  would have printed them in full in any stray log line or traceback — never actually
  logged anywhere today (checked), but a landmine. Fixed with `field(repr=False)` on both,
  tested. Also verified: no `admin_audit`/log call anywhere passes a raw `api_key`; RLS
  policies exist from T104 (Postgres-only, still unverified against a real Postgres — this
  environment only ever ran SQLite); the Phase 5 authz matrix stands as the authz review
- T706 Docs — done: `backoffice/README.md` (setup, **and** a migration runbook for an
  existing single-tenant deployment, verified against the actual bootstrap script's output),
  `backoffice/frontend/README.md` rewritten (was still describing an unbuilt Tailwind+Recharts
  stack), root `README.md`'s repo-layout/quickstart/status sections de-staled

## 8. Constitution compliance

| Principle | Impact |
|---|---|
| I — Frictionless UX | Write path must add <5 ms to a turn (T304, T704). The latency panel makes the 2s target *measurable* for the first time |
| II — Platform-agnostic adapter | Tenant config is generic (`platform` + credentials blob); no PrestaShop specifics leak into analytics, admin, or UI code |
| III — Explicit confirmation | Untouched. The backoffice adds *supervision* of it (Confirmations page) — no admin endpoint may mutate shopper carts |
| IV — Test-first & contracts | Admin OpenAPI contract before endpoints (T504); tenant-isolation (T107) and rollup-correctness (T406) tests are blocking gates |
| V — Observability | This feature *is* Principle V matured: durable, queryable, per-tenant, with stdout JSON retained as the unconditional fallback |
| VI — Rule-based promos | Rules move into per-tenant storage but stay deterministic and human-editable — the editor shows exactly the rules that will run |

**Amendment needed**: the constitution has no data-retention/privacy principle. Storing shopper
message text warrants one (Principle VII — Data Minimization & Retention) before Phase 3 lands.

## 9. Risks

1. **Numbers nobody trusts** — a dashboard that disagrees with the store's own reports is worse
   than none. Mitigation: T406 golden-fixture tests, and every commerce metric reconciled
   against adapter-confirmed orders only, never against proposals.
2. **Retrofitting `tenant_id`** — cheap now (no durable data exists yet), expensive later. This
   is why tenancy is Phase 2, before the event pipeline.
3. **Credential blast radius** — one Postgres row now unlocks a merchant's store API. Encrypted
   at rest, write-only in the API, masked in the UI, KMS-ready, and T705 reviews it.
4. **Import-time singletons** — `src/api/chat.py` builds everything at module import; T203 is
   the riskiest refactor in the plan. T209 (existing suites unchanged) is its safety net.
5. **Scope** — Phases 1–4 alone deliver real value via SQL/Metabase; Phases 5–6 are the
   polished surface. Ship in that order and the feature is useful before it's finished.

## 10. Suggested sequencing

Phases 1→2→3 are strictly ordered. Phase 4 can start once T307 lands. Phases 5 and 6 overlap
once T504 (the contract) is agreed — the UI can build against the OpenAPI schema before the
endpoints exist.

**Recommended first slice**: T101–T107 + T201–T204 + T301–T307. That is the whole "store the
right data, per tenant" problem, and everything after it is reading what's already there.

## 11. Addendum — real Groq LLM + full-journey E2E test

Landed after Phase 7, on request: `FreeTierHostedLLMClient.parse_turn()` (previously
`NotImplementedError` — the entire assistant ran on `rule-based-stub` until now) now really
calls Groq's OpenAI-compatible tool-calling API, with the fixed 9-action tool schema
mirroring `RuleBasedStubClient`'s vocabulary exactly (research.md §9.3's capability boundary
unchanged — still zero LLM-callable tools that map to a mutation), a resilient fallback to
`search_products` on any failure, and one retry on 429. This unblocked two things Phase 3
explicitly deferred pending a real LLM call: `assistant_event` gained real
`llm_provider`/`llm_model`/`prompt_tokens`/`completion_tokens`/`llm_ms`/`cost_micros`
columns (T306, migration `09d4b3edf433`), and `chatbot/widget` now sends `X-Assistant-Key`
(T207, never built during Phase 2) so a real multi-tenant conversation is actually
reachable from the widget, not just from tests calling `handle_turn()` directly.

A new `e2e/` project (Playwright) drives the full natural-language journey — discovery →
add-to-cart → confirm → promo → checkout → confirm order — through the real widget against
a real Groq model, then verifies the result in `backoffice/frontend`'s Sessions/Overview/
Funnel pages against the same database. Skips without a real `LLM_API_KEY`, mirroring
`test_adapter_contract_prestashop.py`'s existing skip pattern; see `e2e/README.md`.

Building this surfaced two real bugs, both fixed:
- `TenantConfig` (`chatbot/backend/src/tenancy/config.py`) held decrypted API keys as plain
  dataclass fields — its default `repr()` would have printed them in a traceback or stray
  log line. Fixed with `field(repr=False)`, predates this addendum but was re-verified here.
- Playwright starts `webServer` processes *before* running `globalSetup` in the installed
  version — a backend that opens its SQLite connection before the database is wiped and
  reseeded keeps reading the deleted-but-still-open inode forever. Fixed by seeding via a
  plain `pretest` npm script (a separate process that fully exits before `playwright test`
  is even invoked) instead of `globalSetup`, with `reuseExistingServer` hardcoded `false` so
  a server left over from a previous run is never reused against fresher seed data.

Not done: this was verified with the skip-path only (harness, seeding, all four servers,
tenant resolution, backoffice reachability all proven working) — the real-Groq assertions
in `shopping-journey.spec.ts` need a human to run them locally with their own key, since
obtaining one without it appearing in an AI session transcript isn't possible from here.
