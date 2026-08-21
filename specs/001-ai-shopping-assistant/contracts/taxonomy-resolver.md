# Contract: Taxonomy Resolver

Governs how the assistant maps a shopper's free-text category/attribute terms (e.g.
"t-shirt", "red") onto the connected store's real, current vocabulary before ever using them
as a `search_products` filter (spec FR-017, research.md §9). This is a deterministic,
non-LLM component — the LLM never sees the full taxonomy and never decides resolution
outcomes itself.

## Dependencies

Two new read-only `CommerceAdapter` methods back this contract:

- `list_categories() -> list[Category]` — the store's real category tree (id, name, parent).
- `list_attributes() -> list[AttributeGroup]` — the store's real attribute groups and values
  in use (e.g., group "Color" → values ["Red", "Burgundy", "Blue", ...]).

Both are read-only, cacheable (see `TaxonomySnapshot`, data-model.md), and subject to the
same `AdapterUnavailableError` behavior as other read-only adapter methods
(contracts/commerce-adapter.md) — if the store can't be reached and no cached
`TaxonomySnapshot` exists yet, taxonomy resolution degrades to `unsupported` (plain-text
fallback only), never to guessing a filter.

## `resolve_category(term: str) -> ResolutionResult`

- **Contract**:
  - MUST normalize `term` (lowercase, basic singular/plural folding) and match it against
    the cached `TaxonomySnapshot`'s category names plus a small curated per-store synonym
    table (e.g., "tee"/"tshirt" → "T-Shirts").
  - MUST return `status="exact"` with one `category_id` when exactly one confident match
    exists.
  - MUST return `status="ambiguous"` with 2+ `candidates` when more than one plausible
    match exists (e.g., "tops" matching both "T-Shirts" and "Tank Tops").
  - MUST return `status="unsupported"` with no candidates when nothing matches — callers
    MUST NOT invent a category ID in this case.
  - MUST NOT consult an LLM call to make this decision — resolution is deterministic so it
    is unit-testable without any LLM provider.

## `resolve_attribute_value(attribute_group: str, term: str) -> ResolutionResult`

- **Contract**:
  - Same `exact` / `ambiguous` / `unsupported` contract as `resolve_category`, scoped to one
    attribute group (e.g., group="Color", term="maroon" → likely `ambiguous` between "Red"
    and "Burgundy", or `unsupported` if neither is a configured synonym).
  - MUST NOT assert that the resolved value is valid for any *specific* product/category —
    that is confirmed only by the subsequent live `search_products`/`get_product` call
    (research.md §9.2). This method only answers "does this term correspond to something in
    the store's vocabulary at all."

## `ResolutionResult` (shared return shape)

| Field | Type | Notes |
|---|---|---|
| status | enum | `exact` \| `ambiguous` \| `unsupported` \| `stale` |
| resolved_id | string \| null | Set only when `status == "exact"` |
| candidates | list[{id, display_label}] | Set when `status == "ambiguous"`; empty otherwise |
| snapshot_age_seconds | int | How old the underlying `TaxonomySnapshot` was at resolution time (for `stale` handling, §9.2) |

## Behavior the dialogue layer MUST implement around this contract

- `status == "exact"` → use `resolved_id` as a `search_products`/`get_product` filter, then
  treat the *returned products* (not the resolution itself) as the source of truth.
- `status == "ambiguous"` → ask one targeted clarifying question listing the candidates by
  their real display labels (never invented ones).
- `status == "unsupported"` → either ask a clarifying question, or run a plain-text
  `search_products(query=<raw term>)` fallback whose results MUST be labeled to the shopper
  as an approximate/keyword match, not asserted as satisfying the originally requested
  filter (spec FR-017; closes red-team challenge #5, "fallback defeats the safety promise").
- `status == "stale"` (a live search using an `exact` resolution returned zero results) →
  trigger a background refresh of the `TaxonomySnapshot` and re-resolve once, rather than
  reporting "no such products" on what may be outdated grounding data.

## Contract tests MUST cover

- Exact match (e.g., "t-shirt" → "T-Shirts").
- Ambiguous match (e.g., "tops" → multiple categories).
- Unsupported term with fallback labeling ("maroon" with no configured synonym → plain-text
  search, response explicitly marked approximate).
- Stale resolution (an `exact` category id whose live `search_products` call returns zero
  results) triggering a snapshot refresh + one re-resolution attempt, not a false "no
  products" report.
- No path exists by which this component (or the dialogue layer around it) can turn a
  resolution into a `search_products` filter that wasn't confirmed present in the current
  `TaxonomySnapshot` or synonym table — asserted via a fuzz-style test feeding nonsense
  terms and confirming only `unsupported`/plain-text-fallback ever results, never an
  invented filter ID.
