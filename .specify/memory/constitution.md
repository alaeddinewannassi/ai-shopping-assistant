# AI Shopping Assistant Constitution

## Core Principles

### I. Conversational-First, Frictionless UX
The assistant is the primary way a shopper interacts with the store: it MUST be able to
answer questions, recommend/filter products, and change navigation (search, category,
product page) purely from natural-language turns. Every response MUST move the shopper
closer to a decision (no dead-end answers). Latency for a conversational turn MUST stay
within standard chat-assistant expectations (target: perceived response start < 2s).

### II. Platform-Agnostic Commerce Adapter
All e-commerce platform integration (PrestaShop, or any other backend) MUST go through a
single Commerce Adapter interface (catalog search, product detail, cart CRUD, promo/coupon
validation, checkout/order creation). Core assistant logic MUST NOT contain
platform-specific code (no direct PrestaShop Webservice/DB calls outside the adapter
implementation). Adding a new platform MUST only require a new adapter implementation,
never changes to assistant/dialogue logic. Reference/dev environment MUST be a
containerized store (Docker Compose) so the adapter can be exercised end-to-end without a
production dependency.

### III. Explicit Confirmation Before Mutating Actions (NON-NEGOTIABLE)
Any action that mutates shopper state — add/remove/update cart line, apply a promo code, or
place an order — MUST be preceded by a clear recap (items, quantities, prices, discounts,
total) and MUST NOT execute until the user explicitly confirms. Navigation changes
(browsing, filtering, viewing a product) are read-only and do not require confirmation.
Checkout is a distinct, final confirmation step separate from cart-edit confirmations.

### IV. Test-First & Adapter Contract Testing (NON-NEGOTIABLE)
Every Commerce Adapter method MUST have a contract test that runs against the dockerized
reference store before it is considered done. Dialogue/agent behaviors (intent routing,
recap generation, promo strategy selection) MUST have scenario-based tests written and
reviewed before implementation (Red-Green-Refactor). No PR merges with failing contract or
scenario tests.

### V. Observability & Auditability of Agent Actions
Every assistant-initiated action (navigation change, cart mutation, promo application,
checkout) MUST be logged with: triggering user intent, action taken, adapter call(s) made,
and outcome. Logs MUST be structured (JSON) and sufficient to reconstruct "why did the
assistant do X" for support/debugging, without storing raw payment credentials or secrets.

### VI. Transparent, Rule-Based Promotion Strategy
Promo code selection/eligibility logic MUST be deterministic and rule-based (e.g.
cart-value thresholds, category/segment targeting, stackability rules), fully described in
the feature spec/plan, and never fabricated or guessed by the assistant. The assistant MUST
only offer codes that pass the platform's own validation (via the adapter) — it MUST NOT
claim a discount is applied unless the adapter confirms it. No dark patterns: discounts
MUST be explained in the recap, and the shopper MUST be able to decline them.

## Technology & Integration Constraints

- Reference e-commerce backend runs via Docker (e.g., official PrestaShop image) so the
  full flow (search → cart → promo → checkout) is testable locally/CI without touching a
  live store.
- The Commerce Adapter interface is the only integration seam; a "generic/mock adapter"
  MUST exist for fast tests independent of the Docker store.
- Conversation/dialogue state (current cart draft, pending confirmation, navigation
  context) is tracked explicitly and MUST be inspectable for debugging.
- Secrets (store API keys/tokens) MUST be provided via environment/config, never hard-coded
  or logged.

## Development Workflow

- Features are developed spec-first using Spec Kit: `/speckit-specify` →
  (`/speckit-clarify`) → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`.
- Each user-facing capability (navigation, add-to-cart, recap/checkout, promo strategy)
  ships as an independently testable user story/slice per the spec template.
- Quality gates before merge: adapter contract tests pass against the Docker reference
  store, scenario tests for dialogue behaviors pass, and no mutating action is reachable
  without a confirmation step.

## Governance

This constitution supersedes ad-hoc practices for this project. Amendments require an
updated version, a documented rationale, and re-validation of in-flight specs/plans against
the changed principle(s). All specs, plans, and task lists MUST be checked for compliance
with these principles before implementation begins; unjustified complexity or a bypassed
confirmation step MUST be flagged and resolved before merge.

**Version**: 1.0.0 | **Ratified**: 2026-08-21 | **Last Amended**: 2026-08-21
