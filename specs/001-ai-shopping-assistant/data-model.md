# Phase 1 Data Model: AI Shopping Assistant for E-Commerce

Entities are derived from the feature spec's Key Entities section, refined with the fields
needed to support navigation, cart mutation with confirmation, checkout recap, and promo
strategy evaluation. Fields marked "(adapter-sourced)" are read from/written through the
Commerce Adapter, not owned by this feature's own storage.

## Product (adapter-sourced)

Represents a catalog item the shopper can discover or navigate to.

| Field | Type | Notes |
|---|---|---|
| id | string | Store-assigned product identifier |
| name | string | Display name |
| category | string | Primary category path/name |
| price | decimal | Current unit price, in store currency |
| variants | list[Variant] | e.g., size/color combinations, each with its own stock status |
| in_stock | bool | Derived from variant stock or product-level stock if no variants |
| description | string | Short description, optional, for recap/discovery display |

### Variant

| Field | Type | Notes |
|---|---|---|
| id | string | Store-assigned variant/combination id |
| attributes | map[string,string] | e.g., `{size: "M", color: "Blue"}` |
| price_delta | decimal | Optional price adjustment vs. base product price |
| in_stock | bool | Availability for this specific variant |

## Cart (adapter-sourced)

The shopper's in-progress selection for the current session.

| Field | Type | Notes |
|---|---|---|
| id | string | Store-assigned cart identifier |
| session_id | string | Links to the owning Conversation Session |
| lines | list[CartLine] | Current cart contents |
| applied_promo_codes | list[string] | Codes currently applied and store-confirmed |
| subtotal | decimal | Sum of line totals before discounts |
| discount_total | decimal | Sum of applied promo discounts |
| grand_total | decimal | subtotal - discount_total (+ any store-computed fees/tax) |

**Validation rules**: `grand_total` is always computed by the adapter/store, never by the
assistant — the assistant only displays it. A Cart with zero lines is valid but MUST be
treated as "empty" for checkout purposes (FR-008, Edge Cases).

### CartLine (adapter-sourced)

| Field | Type | Notes |
|---|---|---|
| product_id | string | References Product |
| variant_id | string \| null | References Variant, if applicable |
| quantity | int | MUST be >= 1 |
| unit_price | decimal | Price at time of adding (may be re-validated at checkout) |
| line_total | decimal | quantity * unit_price |

## PromoCode (adapter-sourced, read/validate only)

| Field | Type | Notes |
|---|---|---|
| code | string | The code text shopper/assistant would submit |
| description | string | Human-readable benefit description, if available from store |
| valid | bool | Result of the store's own validation for the *current* cart |
| discount_amount | decimal \| null | Store-computed discount if valid, else null |
| reason_invalid | string \| null | Store-provided reason if not valid (expired, ineligible, etc.) |

**Validation rules**: This feature never sets `valid`/`discount_amount` itself — both are
always the direct result of `adapter.validate_promo()` / `adapter.apply_promo()` (Principle
VI, FR-012).

## PromoStrategy (owned by this feature)

Declarative rule set used to decide when/what to proactively suggest.

| Field | Type | Notes |
|---|---|---|
| rule_id | string | Unique rule identifier |
| condition | expression | e.g., `subtotal >= 50`, `first_order == true`, `category == "jackets"` |
| target_code | string | The PromoCode.code this rule suggests when its condition matches |
| priority | int | Used to pick among multiple matching rules |
| stackable_with | list[string] | rule_ids this rule's code may be combined with; empty = exclusive |

**Validation rules**: Multiple matching rules are resolved by `priority` then
`stackable_with`; the engine only ever *suggests* — the store's `validate_promo` is still
authoritative on whether the code actually applies (see Edge Cases: "two promo rules match").

## Order (adapter-sourced)

The finalized result of a confirmed checkout.

| Field | Type | Notes |
|---|---|---|
| id | string | Store-issued order identifier/number |
| lines | list[CartLine] | Snapshot of confirmed line items |
| applied_promo_codes | list[string] | Snapshot of codes applied at order time |
| grand_total | decimal | Final confirmed total |
| created_at | datetime | Order creation timestamp |

## ConversationSession (owned by this feature, Redis-backed)

Tracks the ongoing shopper interaction.

| Field | Type | Notes |
|---|---|---|
| session_id | string | Primary key |
| cart_id | string \| null | Reference to the adapter-side Cart, once created |
| navigation_context | object | Current category/search/product view state |
| pending_action | PendingAction \| null | The one in-flight action awaiting confirmation |
| created_at / updated_at | datetime | For TTL/session hygiene |

### PendingAction (owned by this feature)

The structural gate implementing Principle III.

| Field | Type | Notes |
|---|---|---|
| action_type | enum | `add_cart_item` \| `update_cart_item` \| `remove_cart_item` \| `apply_promo` \| `checkout` |
| parameters | object | Action-specific parameters (product/variant/qty, code, etc.) |
| recap_text | string | The human-readable recap shown to the shopper for this exact action |
| created_at | datetime | Used to detect staleness (re-validate if cart/stock/price changed since) |
| confirmed | bool | Set true only by an explicit shopper confirmation turn |

**Validation rules**: No adapter mutation method may be invoked unless a `PendingAction` with
matching `action_type`/`parameters` exists and `confirmed == True` at the moment of
execution; if store state (price/stock/promo) has changed since `created_at`, the action is
invalidated and a fresh `PendingAction` (with updated recap) MUST be created instead (FR-009,
US3 Scenario 4). For any cart-mutation `action_type`, `parameters` MUST include a concrete
`product_id`/`variant_id` resolved from a known `search_products`/`get_product` result — a
`PendingAction` MUST NOT be created from an ambiguous reference ("the red one") without that
resolution step first narrowing it to one item (FR-019, research.md §9.4); the action is
also invalidated (not reused) the moment the conversation moves on to a different product/
variant/quantity, so a later bare "yes" can only confirm the most recent, still-relevant
proposal.

## CatalogSnapshot (owned by this feature, Redis-backed, ephemeral cache)

Read-only fallback cache used only when the Commerce Adapter raises
`AdapterUnavailableError` for a discovery/navigation call (research.md §8, FR-016). Never
consulted for cart, promo, or checkout decisions.

| Field | Type | Notes |
|---|---|---|
| cache_key | string | Derived from the search query/filters or product id it caches |
| products | list[Product] | Last successfully fetched result for that key |
| fetched_at | datetime | When this snapshot was captured |
| ttl_seconds | int | Short TTL (a few minutes); expired entries are treated as absent, not served |

**Validation rules**: A `CatalogSnapshot` MUST only ever be returned to a shopper alongside
an explicit "this may be outdated" disclaimer (FR-016); it MUST NOT be written to or read by
any code path that also touches `Cart`, `PromoCode`, or `Order` — the cache exists purely to
keep read-only browsing responsive during a transient outage, not to approximate live
commerce state.

## TaxonomySnapshot (owned by this feature, Redis-backed, short-TTL cache)

Distinct from `CatalogSnapshot` (outage fallback) — this cache exists for *normal-operation*
grounding of free-text terms against the store's real vocabulary (FR-017, research.md §9),
regardless of whether the adapter is currently reachable.

| Field | Type | Notes |
|---|---|---|
| categories | list[Category] | id, name, parent_id — from `list_categories()` |
| attribute_groups | list[AttributeGroup] | group name → list of real attribute values in use, from `list_attributes()` |
| synonym_table | dict[str, str] | Small curated per-store alias map (e.g. "tee" → "T-Shirts"); maintained outside the LLM, editable config, not learned |
| fetched_at | datetime | When this snapshot was last refreshed |
| ttl_seconds | int | Short TTL; on expiry, refreshed opportunistically on next resolution call |

**Validation rules**: A `TaxonomySnapshot` MUST only ever be consulted by the
`TaxonomyResolver` (contracts/taxonomy-resolver.md) to produce a `ResolutionResult`; it is
never shown to the shopper directly and never treated as proof that a specific product/
variant combination exists or is in stock — only the live `search_products`/`get_product`
result is authoritative for that (research.md §9.2).

### ResolutionResult (transient, not persisted — returned in-process by TaxonomyResolver)

| Field | Type | Notes |
|---|---|---|
| status | enum | `exact` \| `ambiguous` \| `unsupported` \| `stale` |
| resolved_id | string \| null | Set only when `status == "exact"` |
| candidates | list[{id, display_label}] | Set when `status == "ambiguous"` |
| snapshot_age_seconds | int | Age of the `TaxonomySnapshot` used, for `stale`-triggered refresh logic |

## Commerce Adapter Binding (owned by this feature, config)

| Field | Type | Notes |
|---|---|---|
| adapter_type | enum | `prestashop` \| `mock` (extensible) |
| base_url | string | Store base URL (PrestaShop instance) |
| api_key | secret | Webservice key, provided via environment/config, never logged |

## Entity Relationships

```text
ConversationSession 1---0..1 PendingAction
ConversationSession 1---0..1 Cart (via cart_id)
Cart 1---N CartLine
CartLine N---1 Product
CartLine N---0..1 Variant
Cart N---N PromoCode (applied_promo_codes)
PromoStrategy N---1 PromoCode (target_code)
Cart 1---0..1 Order (on confirmed checkout)
```
