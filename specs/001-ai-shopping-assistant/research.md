# Phase 0 Research: AI Shopping Assistant for E-Commerce

All open questions below were resolved with a documented decision so no
`NEEDS CLARIFICATION` markers remain going into Phase 1.

## 1. Reference e-commerce platform & integration mechanism

**Decision**: Use the official PrestaShop Docker image (`prestashop/prestashop`) plus a
MySQL container as the reference/dev store, integrated via PrestaShop's built-in Webservice
REST API (key-based auth, enabled per-resource: products, categories, carts, cart_rules,
orders).

**Rationale**: PrestaShop ships an official Docker image and a documented REST Webservice,
which lets the whole flow (catalog → cart → promo/cart-rule → order) be exercised without
touching a production store, satisfying the constitution's containerized-reference-store
requirement. The Webservice API is stable and platform-agnostic enough conceptually that the
same call shapes (list/get/create/update resource) map cleanly onto a generic Commerce
Adapter interface.

**Alternatives considered**:
- Direct DB access to PrestaShop's MySQL schema — rejected: couples the assistant to
  PrestaShop's internal schema, violates Platform-Agnostic Adapter principle, and bypasses
  business rules (stock, pricing, promo) enforced by PrestaShop's own application layer.
- A GraphQL/headless-commerce middle layer (e.g., a separate PIM) — rejected as unnecessary
  MVP scope; can be revisited if multi-platform support is prioritized later.

## 2. Commerce Adapter interface shape

**Decision**: Define a single Python `Protocol`/ABC (`CommerceAdapter`) with methods:
`search_products(query, filters) -> list[Product]`, `get_product(id) -> Product`,
`get_cart(session_id) -> Cart`, `add_cart_item(...)`, `update_cart_item(...)`,
`remove_cart_item(...)`, `validate_promo(code, cart) -> PromoValidation`,
`apply_promo(code, cart) -> Cart`, `checkout(cart) -> Order`. All dialogue/agent logic only
calls this interface; `PrestaShopAdapter` and `MockAdapter` are its only implementations for
this feature.

**Rationale**: A narrow, verb-based interface (rather than exposing raw REST responses)
keeps platform-specific mapping/parsing entirely inside the adapter, per Principle II, and
gives contract tests a small, well-defined surface to exercise identically against both
implementations.

**Alternatives considered**: Exposing the adapter as a generic "raw resource" pass-through
(get/list/create on arbitrary PrestaShop resources) — rejected: leaks platform concepts
(cart_rules, webservice resource names) into the agent layer, violating Principle II.

## 3. Dialogue / intent handling approach

**Decision**: Use an LLM with function/tool-calling to map natural-language turns to a small,
fixed set of structured actions (`search_products`, `navigate_to`, `propose_add_to_cart`,
`propose_update_cart`, `propose_remove_from_cart`, `request_checkout`, `apply_promo`,
`decline_pending_action`, `confirm_pending_action`), rather than open-ended free-form code
generation or execution.

**Rationale**: Constraining the model to a fixed action vocabulary makes the confirm-before-
mutate gate (Principle III) enforceable in ordinary code (the pending-action state machine),
not dependent on the model "remembering" to ask — mutations are structurally impossible
without passing through `pending.py`.

**Alternatives considered**: A pure rule-based/NLU intent classifier (no LLM) — rejected as
insufficiently flexible for open-ended shopping language; a fully autonomous agent that can
call any tool without a fixed action set — rejected as it would make the confirmation gate
advisory instead of structural.

## 4. Pending-action / confirmation state machine

**Decision**: Any action classified as mutating (add/update/remove cart line, apply promo,
place order) is first turned into a `PendingAction` object (action type + parameters + a
rendered recap string) stored in the Conversation Session. The *only* code path that calls a
mutating adapter method is the handler for an explicit `confirm_pending_action` intent
matched against the currently stored `PendingAction`; any other user turn clears/replaces the
pending action instead of executing it.

**Rationale**: This directly implements Principle III as a structural guarantee (testable:
"no adapter mutation call occurs unless `PendingAction.confirmed == True`") rather than a
prompting convention.

**Alternatives considered**: Confirming via a model-generated "double check" question with no
enforced state — rejected: relies on the model behaving correctly every time, not verifiable
by a unit test.

## 5. Promo strategy representation & evaluation

**Decision**: Promo Strategy rules are declarative data (YAML/JSON) evaluated by a small
rule-engine module: each rule has a condition (e.g., `subtotal >= X`, `first_order == true`,
`category in [...]`), a target promo code, and a priority/stackability flag. The engine
evaluates the current Cart/session against all rules, and — for any candidate — calls
`adapter.validate_promo()` before ever telling the shopper a code is usable.

**Rationale**: Keeps "when to suggest" (business policy, changeable without code changes)
separate from "is it actually valid" (always delegated to the store), directly satisfying
Principle VI (transparent, rule-based, never fabricated).

**Alternatives considered**: Letting the LLM invent/guess eligible promo codes from
conversation context — explicitly rejected, this is exactly the "fabricated discount"
failure mode the constitution forbids.

## 6. Session/state storage

**Decision**: Redis, keyed by session id, storing navigation context, in-progress cart
reference, and the current `PendingAction` (if any), with a short TTL for abandoned
sessions.

**Rationale**: Lightweight, fast, and appropriate for ephemeral conversational state; avoids
standing up a new system-of-record database when PrestaShop's MySQL already owns durable
cart/order data — consistent with the Storage entry in Technical Context.

**Alternatives considered**: In-process memory only — rejected for anything beyond a single
dev process (no multi-instance/service-restart resilience); a new relational DB — rejected as
unnecessary given PrestaShop already persists carts/orders.

## 7. Audit logging format

**Decision**: Structured JSON log lines emitted for every navigation change, cart mutation,
promo suggestion/application, and checkout action, each including: timestamp, session id,
triggering intent, action taken, adapter call(s) + result summary, and outcome
(success/failure/declined).

**Rationale**: Directly satisfies Principle V; JSON keeps logs machine-parseable for
debugging/support tooling without requiring a dedicated logging backend for the MVP.

**Alternatives considered**: Free-text logs — rejected as harder to query/reconstruct
decisions from, which the constitution explicitly requires.
