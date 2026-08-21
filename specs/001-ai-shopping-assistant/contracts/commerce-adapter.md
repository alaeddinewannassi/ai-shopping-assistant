# Contract: Commerce Adapter Interface

This is the single interface every e-commerce platform integration (starting with
PrestaShop) MUST implement, per Constitution Principle II. Both `PrestaShopAdapter` and
`MockAdapter` MUST satisfy this contract, and `tests/contract/` MUST exercise every method
below against the dockerized PrestaShop reference store (plus the Mock, for fast CI).

## `search_products(query: str, filters: SearchFilters) -> list[Product]`

- **Input**: free-text `query` (optional) and structured `filters` (category, min/max price,
  attributes).
- **Output**: list of `Product` (see data-model.md), possibly empty.
- **Contract**:
  - MUST NOT mutate any store state (read-only).
  - MUST reflect current store stock/price at call time (no caching beyond the request).
  - Empty result set MUST return `[]`, never raise, so the dialogue layer can respond
    gracefully (spec Edge Cases: "no catalog matches").

## `get_product(product_id: str) -> Product`

- **Contract**: read-only; raises a typed `ProductNotFoundError` if the id doesn't exist,
  which the dialogue layer maps to "product no longer available" (spec Edge Cases).

## `get_cart(session_id: str) -> Cart`

- **Contract**: read-only; creates an empty Cart on first access if none exists yet for the
  session (no items, `subtotal == 0`).

## `add_cart_item(cart_id, product_id, variant_id, quantity) -> Cart`

- **Preconditions (enforced by caller, i.e. the agent layer)**: MUST only ever be invoked
  from the `confirm_pending_action` handler with a matching, confirmed `PendingAction`
  (Principle III) — this contract test intentionally calls the adapter directly to verify
  adapter-level behavior in isolation from the agent's confirmation gate.
- **Contract**:
  - MUST validate variant/product availability before mutating; MUST raise a typed
    `OutOfStockError` (no partial mutation) if unavailable, rather than adding it anyway.
  - MUST return the updated `Cart` reflecting the new/incremented line and recomputed
    `subtotal`/`grand_total`.

## `update_cart_item(cart_id, line_id, quantity) -> Cart`

- **Contract**: `quantity == 0` is equivalent to removal (see below); otherwise updates the
  line's quantity and returns the recomputed `Cart`. MUST raise `OutOfStockError` if the new
  quantity exceeds available stock.

## `remove_cart_item(cart_id, line_id) -> Cart`

- **Contract**: Idempotent — removing a line id that no longer exists MUST NOT raise, MUST
  just return the current `Cart` unchanged.

## `validate_promo(code: str, cart_id: str) -> PromoValidation`

- **Contract**:
  - Read-only (does not apply the discount).
  - MUST return `valid: false` with a `reason_invalid` for any code that is unknown,
    expired, already used, or ineligible for the current cart — MUST NOT raise for these
    "normal" invalid cases (only for true transport/system errors).
  - This is the *only* source of truth for "is this code usable" — the promo strategy engine
    MUST call this before ever telling the shopper a suggested code is applicable.

## `apply_promo(code: str, cart_id: str) -> Cart`

- **Contract**: MUST internally re-validate (do not trust a stale prior `validate_promo`
  call) and MUST raise a typed `PromoInvalidError` (not silently ignore) if it is no longer
  valid at apply time; on success, returns the `Cart` with `applied_promo_codes` and
  `discount_total`/`grand_total` updated.

## `checkout(cart_id: str) -> Order`

- **Contract**:
  - MUST re-validate the entire cart (stock, prices, applied promos) immediately before
    order creation; if anything has changed since the cart was last read, MUST raise a typed
    `CartStateChangedError` instead of creating a mismatched order (spec FR-009, US3
    Scenario 4) — the agent layer maps this to "re-present a fresh recap, require a new
    confirmation."
  - On success, MUST return an `Order` with a store-issued `id` and the final confirmed
    totals.
  - MUST be called only after the agent layer's confirmed `PendingAction(action_type=
    "checkout")` — enforced by the agent layer, verified in integration tests, not by this
    adapter method itself.

## Error Types (shared vocabulary across adapters)

| Error | Raised by | Meaning |
|---|---|---|
| `ProductNotFoundError` | get_product | Referenced product id doesn't exist |
| `OutOfStockError` | add/update_cart_item | Requested quantity/variant unavailable |
| `PromoInvalidError` | apply_promo | Code not valid at apply time |
| `CartStateChangedError` | checkout | Cart state changed since last read; re-recap required |

Contract tests MUST assert both the "happy path" return shape and that each error type is
raised under the documented condition, for every adapter implementation.
