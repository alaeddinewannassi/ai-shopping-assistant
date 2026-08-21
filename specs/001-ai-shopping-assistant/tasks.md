# Tasks: AI Shopping Assistant for E-Commerce

**Input**: Design documents from `/specs/001-ai-shopping-assistant/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — Constitution Principle IV (Test-First & Adapter Contract Testing) is
NON-NEGOTIABLE for this project, so contract/integration/unit tests are mandatory tasks, not
optional.

**Organization**: Tasks are grouped by user story so each story is independently
implementable/testable. Implementation order follows functional dependency (US1 →
US2 → US3 → US4), even though US1 and US3 are both Priority P1 — US3 (checkout) cannot be
built or demoed without a cart populated by US2.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on other unfinished tasks)
- **[Story]**: US1 (Discovery/Navigation), US2 (Add to Cart), US3 (Checkout/Recap), US4
  (Promo Strategy)
- Paths follow `plan.md`'s Project Structure: `backend/src/`, `backend/tests/`, `widget/`,
  `docker/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repository scaffolding so any story can start being implemented.

- [x] T001 Create repository layout per plan.md: `backend/src/{adapters,agent,promo,session,logging,api}`,
      `backend/tests/{contract,integration,unit}`, `widget/src`, `widget/tests`, `docker/`
- [x] T002 Initialize `backend/` as a Python 3.11 project (`pyproject.toml`/`requirements.txt`)
      with FastAPI, Pydantic, httpx, redis-py, pytest declared as dependencies
- [ ] T003 [P] Initialize `widget/` as a minimal TypeScript project (bundler + test runner) for
      the embeddable chat widget
- [ ] T004 [P] Configure linting/formatting for backend (ruff/black) and widget (eslint/prettier)
- [x] T005 Write `docker/docker-compose.yml` with services: `prestashop`, `mysql`, `redis`,
      `assistant-service`, wiring env vars (`PRESTASHOP_BASE_URL`, `PRESTASHOP_API_KEY`, `REDIS_URL`).
      NOTE: config-validated (`docker compose config`) but not yet run end-to-end in this
      environment (Docker daemon unavailable) — see docker/prestashop/README.md for the
      manual Admin steps (Webservice key, cart rules, checkout customer/address/carrier)
      still required after `docker compose up` before the stack is fully usable
- [x] T006 [P] Add `docker/prestashop/` fixture notes + a seed script/checklist for demo catalog
      (2+ categories, 1+ product with variants, 1 out-of-stock product) and demo cart rules
      (`WELCOME10`, `BIGCART15`) per quickstart.md
- [x] T007 [P] Create `backend/.env.example` documenting `PRESTASHOP_BASE_URL`,
      `PRESTASHOP_API_KEY`, `REDIS_URL`, and `LLM_PROVIDER` (default `free-tier-hosted` |
      `rule-based-stub` | `hosted-paid`) + `LLM_API_KEY` (a free Groq/Gemini free-tier key
      for the default; `rule-based-stub` needs no key at all) per research.md §3a (never
      commit real secrets)

**Checkpoint**: Repo builds/lints; `docker compose up` brings up all services (PrestaShop may
still need manual first-run install per quickstart.md).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure every user story depends on — the adapter interface, session
store, pending-action gate, and audit logging. **No user story implementation may begin until
this phase is complete**, per Constitution Principles II, III, and V.

- [x] T008 Define core data model types in `backend/src/models.py` (or `models/` package):
      `Product`, `Variant`, `Cart`, `CartLine`, `PromoCode`/`PromoValidation`, `Order`,
      per data-model.md
- [x] T009 Define the `CommerceAdapter` interface (Protocol/ABC) in
      `backend/src/adapters/base.py` with all methods and error types from
      `contracts/commerce-adapter.md` (`search_products`, `get_product`, `list_categories`,
      `list_attributes`, `get_cart`, `add_cart_item`, `update_cart_item`,
      `remove_cart_item`, `validate_promo`, `apply_promo`, `checkout`;
      `ProductNotFoundError`, `OutOfStockError`, `PromoInvalidError`,
      `CartStateChangedError`, `AdapterUnavailableError`)
- [x] T009a [P] Implement the resilience wrapper in `backend/src/adapters/resilience.py`
      per research.md §8: short timeout + limited retry + simple circuit breaker around any
      `CommerceAdapter` call, normalizing transport/timeout failures into
      `AdapterUnavailableError` (never masking it as a business error like
      `ProductNotFoundError`)
- [x] T010 [P] Implement `MockAdapter` in `backend/src/adapters/mock.py` (in-memory catalog/
      cart/promo/order state) satisfying the full `CommerceAdapter` contract, for fast
      tests, including a test-only mode to simulate `AdapterUnavailableError` on demand
- [x] T011 [P] Contract test suite in `backend/tests/contract/test_adapter_contract.py`,
      parametrized to run against both `MockAdapter` and `PrestaShopAdapter` (skips
      PrestaShop cases if the Docker store isn't reachable), covering every method/error in
      `contracts/commerce-adapter.md`, including `AdapterUnavailableError` on a simulated
      outage (e.g., unreachable URL/short timeout) for both read and mutating methods
- [x] T012 Implement `PrestaShopAdapter` in `backend/src/adapters/prestashop.py` using
      httpx against the PrestaShop Webservice REST API (products, categories, carts,
      cart_rules, orders resources), satisfying T011's contract tests. NOTE: written from
      PrestaShop's official webservice docs but not yet integration-tested against a live
      store in this environment (Docker daemon unavailable) — run `docker compose up` +
      `pytest tests/contract/test_adapter_contract_prestashop.py` to validate before
      trusting it against a real deployment; see the module docstring for the highest-risk
      areas (combination/attribute resolution, order creation prerequisites)
- [x] T012a [P] Implement `CatalogSnapshot` cache in `backend/src/session/catalog_cache.py`
      per data-model.md: Redis-backed, short TTL, keyed by search query/filters or product
      id, read/write helpers only — this module MUST NOT be imported by any cart/promo/
      checkout code path (enforced by a unit test asserting no such import exists)
- [x] T013 [P] Implement `ConversationSession` + `PendingAction` models and Redis-backed
      store in `backend/src/session/store.py` per data-model.md (get/create session,
      read/write pending action, TTL for abandoned sessions)
- [x] T014 [P] Implement the pending-action state machine in `backend/src/agent/pending.py`:
      `propose(action_type, parameters, recap_text)`, `confirm()`, `decline()`, and a
      staleness check (re-validate if store state changed since `created_at`) — this is the
      single choke point through which any mutating adapter call may be reached
- [x] T014a [P] Implement the swappable `LLMClient` abstraction in
      `backend/src/agent/llm_client.py` per research.md §3a: a common interface (turn text +
      fixed action schema → structured action call) with three selectable implementations
      chosen via `LLM_PROVIDER` — `FreeTierHostedLLMClient` (**default**; free-tier
      OpenAI-compatible tool-calling API such as Groq or Gemini's free tier, needs a free
      `LLM_API_KEY`), `RuleBasedStubClient` (free deterministic keyword matcher, used for
      automated tests so the suite runs with zero LLM cost/dependency), and
      `HostedPaidLLMClient` (stubbed only — for a possible future production upgrade, out of
      scope for this internship deliverable)
- [x] T015 [P] Implement structured JSON audit logging in `backend/src/logging/audit.py`
      (timestamp, session id, intent, action, adapter result summary, outcome) and wire a
      helper the agent layer will call for every navigation/cart/promo/checkout action
- [x] T016 Implement FastAPI app skeleton in `backend/src/api/chat.py`
      (`POST /chat`, `GET /health`) wired to session store + a placeholder dialogue handler
- [x] T017 [P] Unit tests for the pending-action state machine in
      `backend/tests/unit/test_pending.py`: no mutation reachable without `confirmed=True`;
      staleness invalidation creates a fresh pending action
- [x] T017a [P] Unit tests for `RuleBasedStubClient` in
      `backend/tests/unit/test_llm_client.py`: covers the full fixed action vocabulary
      (research.md §3) deterministically with zero external calls, and a provider-selection
      test verifying `LLM_PROVIDER` correctly instantiates each client type

**Checkpoint**: Foundation ready — adapter contract passes against Mock (and PrestaShop once
available), pending-action gate is unit-tested, audit logging and session store work. User
story implementation can now begin.

---

## Phase 3: User Story 1 - Conversational Product Discovery & Navigation (Priority: P1) 🎯 MVP

**Goal**: Shopper can browse/search/navigate the catalog purely via natural language, with
graceful handling of ambiguity and no-results.

**Independent Test**: Issue natural-language browse/search/filter requests against the
reference store; verify correct matching products/navigation with no cart/account
dependency (per spec.md US1 Independent Test).

### Tests for User Story 1 ⚠️ (write first, confirm they fail, then implement)

- [x] T018 [P] [US1] Integration test "search/filter returns matching products" in
      `backend/tests/integration/test_us1_discovery.py` (spec US1 Scenario 1)
- [x] T019 [P] [US1] Integration test "navigate to named category/product" in
      `backend/tests/integration/test_us1_discovery.py` (spec US1 Scenario 2)
- [x] T020 [P] [US1] Integration test "ambiguous request triggers one clarifying question" in
      `backend/tests/integration/test_us1_discovery.py` (spec US1 Scenario 3)
- [x] T021 [P] [US1] Integration test "no catalog matches → plain message + alternatives, no
      dead-end navigation" in `backend/tests/integration/test_us1_discovery.py` (spec US1
      Scenario 4)
- [x] T021a [P] [US1] Integration test "store backend unreachable during discovery → falls
      back to cached Catalog Snapshot with a clear 'may be outdated' disclaimer (or a plain
      'can't search right now' message if nothing is cached yet)" in
      `backend/tests/integration/test_us1_discovery.py` (spec Edge Cases: backend
      unreachable, FR-016, research.md §8)

### Implementation for User Story 1

- [x] T022 [US1] Implement intent parsing for discovery/navigation actions
      (`search_products`, `navigate_to`) in `backend/src/agent/intents.py`, using
      `LLMClient` (T014a) against the fixed action vocabulary from research.md §3 — works
      against any configured provider, including the free `rule-based-stub`
- [x] T023 [US1] Implement ambiguity detection + single clarifying-question generation in
      `backend/src/agent/intents.py` (max one question, per spec FR-003)
- [x] T024 [US1] Implement navigation-context tracking (update
      `ConversationSession.navigation_context`) in `backend/src/agent/dialogue.py`
- [x] T025 [US1] Wire discovery/navigation turns end-to-end in
      `backend/src/agent/dialogue.py`: intent → `adapter.search_products`/`get_product`
      (wrapped by `resilience.py`, T009a) → response, with audit logging (T015) for every
      navigation change (FR-014)
- [x] T025a [US1] Implement degraded-mode handling for discovery reads in
      `backend/src/agent/dialogue.py`: on `AdapterUnavailableError`, attempt
      `catalog_cache.py` (T012a) lookup and reply with results plus an explicit
      "may be outdated" disclaimer; if no snapshot exists, reply plainly that search is
      temporarily unavailable — never fabricate product data (FR-016, research.md §8)
- [x] T026 [US1] Handle empty-result and not-found cases gracefully (friendly message +
      alternatives) in `backend/src/agent/dialogue.py` (FR-015 partial — discovery-time
      unavailability)
- [x] T027 [US1] Expose discovery/navigation turns through `POST /chat` in
      `backend/src/api/chat.py`

**Checkpoint**: User Story 1 fully functional and independently testable/demoable (chat-based
search + navigation, no cart yet).

---

## Phase 4: User Story 2 - Add to Cart with Confirmation (Priority: P2)

**Goal**: Shopper can add/update/remove cart items conversationally, with every mutation
gated by an explicit pre-mutation confirmation (Constitution Principle III).

**Independent Test**: Ask the assistant to add a known product; verify no mutation occurs
before confirmation is shown, and the cart correctly reflects the item only after the
shopper confirms (per spec.md US2 Independent Test).

### Tests for User Story 2 ⚠️

- [x] T028 [P] [US2] Integration test "add-to-cart request produces confirmation, no mutation
      yet" in `backend/tests/integration/test_us2_cart.py` (spec US2 Scenario 1)
- [x] T029 [P] [US2] Integration test "confirming adds the item; cart reflects it" in
      `backend/tests/integration/test_us2_cart.py` (spec US2 Scenario 2)
- [x] T030 [P] [US2] Integration test "declining/changing leaves cart untouched, offers
      corrected option" in `backend/tests/integration/test_us2_cart.py` (spec US2 Scenario 3)
- [x] T031 [P] [US2] Integration test "update quantity / remove line follows same
      confirm-before-mutate flow" in `backend/tests/integration/test_us2_cart.py` (spec US2
      Scenario 4)
- [x] T032 [P] [US2] Integration test "out-of-stock product reports unavailability + in-stock
      alternatives, no mutation" in `backend/tests/integration/test_us2_cart.py` (spec US2
      Scenario 5)
- [x] T032a [P] [US2] Integration test "store backend unreachable when trying to
      add/update/remove a cart item → assistant plainly refuses, no `PendingAction` is
      created/confirmed, no mutation attempted" in `backend/tests/integration/
      test_us2_cart.py` (spec Edge Cases: backend unreachable, FR-016, research.md §8)

### Implementation for User Story 2

- [x] T033 [US2] Implement intent parsing for `propose_add_to_cart`,
      `propose_update_cart`, `propose_remove_from_cart`, `confirm_pending_action`,
      `decline_pending_action` in `backend/src/agent/intents.py`
- [x] T034 [US2] Implement recap text builder for cart-mutation `PendingAction`s in
      `backend/src/agent/recap.py` (product, variant, quantity, unit price)
- [x] T035 [US2] Wire propose→confirm/decline flow in `backend/src/agent/dialogue.py`:
      proposal creates a `PendingAction` (T014) with recap (T034); confirmation is the only
      path that calls `adapter.add_cart_item`/`update_cart_item`/`remove_cart_item`
      (wrapped by `resilience.py`, T009a)
- [x] T035a [US2] Handle `AdapterUnavailableError` on any cart mutation call in
      `backend/src/agent/dialogue.py`: never create/confirm a `PendingAction` for the call
      in question, and reply plainly that the change can't be verified/applied right now —
      no cache fallback, no assumed success (FR-016, research.md §8)
- [x] T036 [US2] Handle `OutOfStockError` from the adapter by reporting unavailability +
      suggesting alternatives (via `search_products`) instead of mutating (FR-015)
- [x] T037 [US2] Add audit logging (T015) for every proposed and executed cart mutation,
      including declines (FR-014)
- [x] T038 [US2] Extend `POST /chat` in `backend/src/api/chat.py` to route
      propose/confirm/decline turns through the dialogue layer

**Checkpoint**: User Stories 1+2 together deliver "discover and build a cart conversationally,
safely" as a demoable increment.

---

## Phase 5: User Story 3 - Checkout with Full Recap & Final Confirmation (Priority: P1)

**Goal**: Shopper reviews a full cart recap and places an order only after explicit final
confirmation, with re-validation if store state changed since the recap was shown.

**Independent Test**: Pre-populate a cart, invoke checkout, verify the recap matches cart
state/totals, and verify the order is only created after explicit final confirmation (per
spec.md US3 Independent Test).

### Tests for User Story 3 ⚠️

- [x] T039 [P] [US3] Integration test "checkout request produces full recap (lines, qty,
      price, discounts, total) + asks for final confirmation" in
      `backend/tests/integration/test_us3_checkout.py` (spec US3 Scenario 1)
- [x] T040 [P] [US3] Integration test "final confirmation places order, returns order id" in
      `backend/tests/integration/test_us3_checkout.py` (spec US3 Scenario 2)
- [x] T041 [P] [US3] Integration test "requesting a change instead of confirming re-enters
      US2 flow and re-presents a fresh recap before allowing checkout again" in
      `backend/tests/integration/test_us3_checkout.py` (spec US3 Scenario 3)
- [x] T042 [P] [US3] Integration test "stock/price/promo changes between recap and
      confirmation trigger re-validation + fresh confirmation, no mismatched order" in
      `backend/tests/integration/test_us3_checkout.py` (spec US3 Scenario 4, FR-009)
- [x] T043 [P] [US3] Integration test "checkout with empty cart informs shopper, offers to
      resume discovery, no recap shown" (spec Edge Cases: empty-cart checkout)

### Implementation for User Story 3

- [x] T044 [US3] Implement full recap builder (lines, qty, unit price, discounts, grand
      total) for the `checkout` `PendingAction` in `backend/src/agent/recap.py`
      (extends T034)
- [x] T045 [US3] Implement `request_checkout` intent + empty-cart short-circuit in
      `backend/src/agent/intents.py` / `dialogue.py`
- [x] T046 [US3] Wire final-confirmation path in `backend/src/agent/dialogue.py`: only a
      confirmed `checkout` `PendingAction` calls `adapter.checkout(cart_id)`
- [x] T047 [US3] Handle `CartStateChangedError` from `adapter.checkout` by invalidating the
      pending action and re-presenting a fresh recap (FR-009) instead of retrying blindly
- [x] T048 [US3] Add audit logging (T015) for checkout recap presentation, confirmation,
      decline/edit, and final order outcome (FR-014)
- [x] T049 [US3] Extend `POST /chat` in `backend/src/api/chat.py` to route
      checkout/final-confirmation turns through the dialogue layer

**Checkpoint**: Full core loop (discover → cart → checkout) is demoable end-to-end against
the dockerized PrestaShop reference store.

---

## Phase 6: User Story 4 - Strategic Promo Code Suggestions (Priority: P3)

**Goal**: Assistant proactively suggests store-validated promo codes per a rule-based
strategy, and can validate/apply shopper-provided codes, without ever fabricating a discount.

**Independent Test**: Configure promo codes/strategy rules, drive a cart into a matching
state, verify the assistant suggests the correct code, and that applying it only updates the
total after store validation (per spec.md US4 Independent Test).

### Tests for User Story 4 ⚠️

- [x] T050 [P] [US4] Unit tests for the promo rule engine (`evaluate`) in
      `backend/tests/unit/test_promo_engine.py`: single match, no match, multiple
      matching/stackable rules, multiple matching/exclusive rules → priority resolution
      (contracts/promo-strategy.md)
- [x] T051 [P] [US4] Integration test "cart matches a rule → assistant proactively suggests
      the applicable code with benefit explanation" in
      `backend/tests/integration/test_us4_promo.py` (spec US4 Scenario 1)
- [x] T052 [P] [US4] Integration test "shopper accepts suggestion → store-validated before
      reflected in recap/total" in `backend/tests/integration/test_us4_promo.py` (spec US4
      Scenario 2)
- [x] T053 [P] [US4] Integration test "shopper declines suggestion → no code applied,
      original total stands" in `backend/tests/integration/test_us4_promo.py` (spec US4
      Scenario 3)
- [x] T054 [P] [US4] Integration test "shopper-provided code validated same way; invalid/
      expired/ineligible reported clearly" in `backend/tests/integration/test_us4_promo.py`
      (spec US4 Scenario 4)
- [x] T055 [P] [US4] Integration test "no rule matches → assistant honestly reports no
      discount available" in `backend/tests/integration/test_us4_promo.py` (spec US4
      Scenario 5)

### Implementation for User Story 4

- [x] T056 [P] [US4] Define `PromoStrategyRule` config format (YAML/JSON) + loader in
      `backend/src/promo/strategy.py` per data-model.md/contracts/promo-strategy.md
- [x] T057 [US4] Implement the rule engine `evaluate()` in `backend/src/promo/engine.py`
      (pure function: cart + session_context + rules → ordered suggestions), per
      contracts/promo-strategy.md
- [x] T058 [US4] Wire proactive suggestion flow in `backend/src/agent/dialogue.py`: call
      `engine.evaluate()`, then `adapter.validate_promo()` before surfacing any suggestion to
      the shopper (never surface an unvalidated candidate)
- [x] T059 [US4] Implement `apply_promo` intent handling: shopper acceptance creates an
      `apply_promo` `PendingAction`; confirmation is the only path calling
      `adapter.apply_promo()`
- [x] T060 [US4] Implement manual/shopper-provided promo code path (bypasses engine,
      goes straight to `validate_promo` → propose → confirm → `apply_promo`)
- [x] T061 [US4] Add audit logging (T015) for every suggestion shown/declined and every
      validate/apply outcome (FR-014)
- [x] T062 [US4] Extend checkout recap (T044) to include any applied promo discount and
      final total reflecting store-confirmed values only

**Checkpoint**: All four user stories independently functional; full spec acceptance
scenarios covered end-to-end.

---

## Phase 7: Widget & Polish

**Purpose**: Shopper-facing surface and cross-cutting hardening once all stories work.

- [ ] T063 [P] Implement minimal embeddable chat widget in `widget/src/` (send/receive chat
      turns against `POST /chat`, render recap/confirmation prompts distinctly from
      read-only responses)
- [ ] T064 [P] Widget smoke test in `widget/tests/` (renders, sends a message, displays a
      response)
- [ ] T065 [P] Add `GET /health` readiness checks for adapter/Redis connectivity in
      `backend/src/api/chat.py`
- [ ] T066 [P] Review all audit log call sites (T015 usages) for completeness against
      FR-014 (every navigation/cart/promo/checkout action logged)
- [ ] T067 Run and fix quickstart.md end-to-end walkthrough against the full Docker Compose
      stack; update quickstart.md with any corrections
- [ ] T068 [P] Add README covering setup, running tests, and running the reference
      environment (links to quickstart.md)

**Checkpoint**: Feature is demoable end-to-end via the widget against the dockerized
PrestaShop reference store, with passing contract/integration/unit tests.

---

## Dependencies & Execution Order

- **Phase 1 (Setup)** → **Phase 2 (Foundational)**: strictly sequential; Foundational tasks
  block all user stories.
- **Phase 3 (US1)** can start immediately after Phase 2. It has no dependency on US2/US3/US4.
- **Phase 4 (US2)** depends on US1 only for *identifying* a product conversationally in
  end-to-end tests; the propose/confirm mutation machinery itself only depends on Phase 2.
  Can start once Phase 2 is done; benefits from US1 being available for realistic scenarios.
- **Phase 5 (US3)** depends on Phase 4 (needs a cart to check out) — must follow US2.
- **Phase 6 (US4)** depends on Phase 4/5 (suggestions surface during shopping and at
  checkout) — should follow US2/US3, though the rule-engine unit tests (T050, T056, T057)
  are independent and can be built in parallel with earlier phases.
- **Phase 7 (Widget & Polish)** depends on all user stories being functional.

Within each user story phase, tasks marked `[P]` touch different files/tests and can be
worked in parallel; unmarked tasks have an in-phase dependency (usually on the immediately
preceding task) and should be done in order.

## Suggested MVP Cut

Phases 1–3 (Setup, Foundational, US1) plus a thin slice of Phase 4 (T028–T035 without full
edge-case coverage) delivers a demoable "conversational discovery + confirmed add-to-cart"
MVP. Full spec compliance requires all phases through Phase 6.
