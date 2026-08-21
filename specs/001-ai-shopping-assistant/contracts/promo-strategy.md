# Contract: Promo Strategy Engine

Internal contract for the `promo/` module (owned by this feature — not a store
integration), defining how proactive promo suggestions are produced and how they must relate
to the Commerce Adapter's authoritative validation, per Constitution Principle VI.

## `evaluate(cart: Cart, session_context: SessionContext, rules: list[PromoStrategyRule]) -> list[PromoSuggestion]`

- **Input**:
  - `cart` — current Cart (subtotal, categories present, etc.)
  - `session_context` — e.g., `is_first_order: bool`, shopper-visible attributes needed by
    rule conditions
  - `rules` — the configured `PromoStrategy` rule set (see data-model.md)
- **Output**: ordered list of `PromoSuggestion { rule_id, code, priority }`, highest priority
  first, already filtered for `stackable_with` conflicts (mutually exclusive rules resolved
  by priority — only one of a conflicting group is returned).
- **Contract**:
  - MUST be a pure function of its inputs (no adapter calls, no side effects) — this is the
    "when/what to suggest" policy layer only.
  - MUST NOT set `valid` or `discount_amount` on any suggestion — those fields do not exist
    at this stage; validity is determined only by the adapter contract
    (`commerce-adapter.md#validate_promo`).
  - If no rule matches, MUST return `[]` (never fabricate a fallback suggestion).

## Suggestion → Shopper flow (agent layer responsibility, referenced here for clarity)

1. `engine.evaluate(...)` returns candidate `PromoSuggestion`s (policy only).
2. For the top suggestion, the agent layer calls
   `adapter.validate_promo(code, cart_id)` (see commerce-adapter.md).
3. Only if `validate_promo` returns `valid: true` does the assistant present the suggestion
   to the shopper (with the store-provided discount amount/description).
4. If `valid: false`, the agent layer MUST NOT surface that candidate to the shopper as a
   suggestion; it MAY silently try the next-priority candidate or suggest none.
5. Shopper acceptance triggers a `PendingAction(action_type="apply_promo")` — subject to the
   same confirm-before-mutate gate as any other mutating action (Principle III); only on
   confirmation does the agent layer call `adapter.apply_promo(...)`.

## Manually-provided codes

- A shopper-provided code (not suggested by the engine) skips step 1–2 above and goes
  straight to `adapter.validate_promo()`, then follows the same confirm → `apply_promo()`
  flow. The engine is never consulted for shopper-provided codes — it only governs proactive
  suggestions.

## Test obligations

- Unit tests (`tests/unit/`) cover the rule engine in isolation: single match, no match,
  multiple matching/stackable rules, multiple matching/exclusive rules (priority
  resolution) — using fixture rules, no adapter/store involved.
- Integration tests (`tests/integration/`) cover the full suggestion → validate → confirm →
  apply flow against the Mock Adapter (fast) and, for at least one scenario, the dockerized
  PrestaShop adapter (confidence that a real store's cart-rule validation is honored).
