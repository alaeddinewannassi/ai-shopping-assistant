# Feature Specification: AI Shopping Assistant for E-Commerce

**Feature Branch**: `001-ai-shopping-assistant`

**Created**: 2026-08-21

**Status**: Draft

**Input**: User description: "an ai assistant that plugs into a PrestaShop (or any e-commerce, via a Docker reference store) that helps users shop interactively: change navigation, add to shopping cart, validate with the user via a recap, and give promo codes with some strategy."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Conversational Product Discovery & Navigation (Priority: P1)

A shopper describes what they're looking for in plain language ("show me running shoes
under $80", "take me to the men's jackets category"). The assistant interprets the request,
searches/filters the catalog, and navigates the shopper to the relevant category, search
results, or product page — without the shopper needing to use menus or filters manually.

**Why this priority**: This is the entry point of every shopping session. Without reliable
conversational navigation and discovery, no other capability (cart, checkout, promos) can be
reached. It is also independently valuable/demoable on its own (a "smart search/navigation"
assistant).

**Independent Test**: Can be fully tested by issuing natural-language browse/search/filter
requests against the reference store and verifying the assistant returns matching products
and/or moves the session to the correct catalog location, with no cart or account
dependency.

**Acceptance Scenarios**:

1. **Given** a shopper is on the home page, **When** they ask for a product by category and
   constraint (e.g., "show me jackets under $100"), **Then** the assistant returns matching
   products from the reference store filtered by that category and price constraint.
2. **Given** a shopper is browsing, **When** they ask to go to a specific category or a
   specific product they mention by name, **Then** the assistant navigates the session to
   that category/product page.
3. **Given** a shopper's request is ambiguous (matches multiple unrelated products/categories
   or is missing needed detail), **When** they submit it, **Then** the assistant asks one
   concise clarifying question instead of guessing silently.
4. **Given** a shopper searches for something with no catalog matches, **When** results come
   back empty, **Then** the assistant says so plainly and offers close alternatives instead
   of navigating nowhere.

---

### User Story 2 - Add to Cart with Confirmation (Priority: P2)

A shopper asks the assistant to add a product (and quantity/variant, if applicable) to their
cart. Before anything is actually added, the assistant shows what it is about to do (item,
variant, quantity, price) and only performs the cart mutation after the shopper confirms.

**Why this priority**: Cart mutation is the first "real" transactional action and the point
where trust matters most — an assistant that silently adds wrong items erodes confidence.
Depends on User Story 1 (discovery/navigation) to identify products, but is independently
testable given a known product.

**Independent Test**: Can be fully tested by asking the assistant to add a specific known
product to the cart and verifying (a) no cart mutation occurs before confirmation is shown,
and (b) the cart in the reference store correctly reflects the item/quantity/variant only
after the shopper confirms.

**Acceptance Scenarios**:

1. **Given** a shopper has identified a product, **When** they ask to add it to the cart,
   **Then** the assistant presents a short confirmation (product, variant, quantity, unit
   price) and waits for explicit approval before mutating the cart.
2. **Given** the assistant has presented an add-to-cart confirmation, **When** the shopper
   approves, **Then** the item is added to the cart in the underlying store and the assistant
   confirms the updated cart contents.
3. **Given** the assistant has presented an add-to-cart confirmation, **When** the shopper
   declines or asks to change quantity/variant, **Then** no cart mutation occurs and the
   assistant offers the corrected option for a new confirmation.
4. **Given** a shopper asks to remove or change the quantity of an existing cart line,
   **When** they submit that request, **Then** the same confirm-before-mutate flow applies.
5. **Given** a requested product/variant is out of stock or unavailable, **When** the shopper
   tries to add it, **Then** the assistant reports unavailability and suggests in-stock
   alternatives instead of adding it.

---

### User Story 3 - Checkout with Full Recap & Final Confirmation (Priority: P1)

When the shopper is ready to complete their purchase, the assistant presents a full recap of
the cart — every line item, quantity, unit price, applied discounts, and the final total —
and only proceeds to place the order in the underlying store after the shopper gives an
explicit final confirmation.

**Why this priority**: This is the moment of highest stakes (money changes hands) and the
ultimate value delivery of the assistant ("help users make it through easily and smoothly").
It is P1 because a shopping assistant without a trustworthy checkout has no real business
value, even though it depends on a populated cart from User Story 2.

**Independent Test**: Can be fully tested by pre-populating a cart in the reference store,
invoking checkout through the assistant, verifying the recap accurately reflects cart state
and totals, and verifying the order is only created in the store after explicit final
confirmation (never before).

**Acceptance Scenarios**:

1. **Given** a shopper has items in their cart and asks to check out, **When** the assistant
   responds, **Then** it presents a recap listing every line item, quantity, unit price, any
   applied discount, and the grand total, and explicitly asks the shopper to confirm.
2. **Given** the recap has been presented, **When** the shopper gives explicit final
   confirmation, **Then** the assistant finalizes the order in the underlying store and
   reports an order confirmation (order id/number) back to the shopper.
3. **Given** the recap has been presented, **When** the shopper asks to change something
   (remove an item, change quantity) instead of confirming, **Then** the assistant updates
   the cart per User Story 2's flow and re-presents a fresh recap before allowing checkout
   again.
4. **Given** the underlying price/stock/promo state changes between recap and confirmation
   (e.g., stock ran out), **When** the shopper confirms, **Then** the assistant re-validates
   against the store, surfaces the discrepancy, and requires a new confirmation rather than
   silently placing a mismatched order.

---

### User Story 4 - Strategic Promo Code Suggestions (Priority: P3)

While shopping or at checkout, the assistant proactively suggests an applicable promo code
based on a defined strategy (e.g., cart-value thresholds, first-order incentives,
category-specific offers), and lets the shopper choose to apply or decline it. Any applied
code is verified against the underlying store before being reflected in the recap/total.

**Why this priority**: Promo strategy adds business value (conversion, average order value)
but is an enhancement on top of an already-working cart/checkout flow, so it can be
delivered after User Stories 1–3 without blocking the assistant's core usefulness.

**Independent Test**: Can be fully tested by configuring one or more promo codes and
strategy rules in the reference store, driving a cart into a state that matches a rule
(e.g., over a spend threshold), and verifying the assistant suggests the correct code, and
that applying it updates the recap total only after the store validates it.

**Acceptance Scenarios**:

1. **Given** a shopper's cart matches a configured promo strategy rule (e.g., subtotal above
   a threshold), **When** the assistant next responds (during shopping or at checkout),
   **Then** it proactively suggests the applicable promo code and explains the benefit.
2. **Given** the assistant has suggested a promo code, **When** the shopper agrees to apply
   it, **Then** the assistant submits it to the underlying store for validation and only
   reflects the discount in the recap/total if the store confirms it is valid.
3. **Given** the assistant has suggested a promo code, **When** the shopper declines, **Then**
   no code is applied and the shopper proceeds with the original total.
4. **Given** a shopper manually provides a promo code the assistant did not suggest, **When**
   they ask to apply it, **Then** the assistant validates it the same way (via the store) and
   reports clearly if it is invalid, expired, or not eligible for the current cart.
5. **Given** no promo rule matches the current cart, **When** the shopper asks if any
   discounts are available, **Then** the assistant honestly reports that none apply rather
   than inventing one.

### Edge Cases

- What happens when the shopper's request could match products in more than one category or
  is otherwise ambiguous? → Assistant asks one targeted clarifying question (see User Story
  1, Scenario 3).
- How does the system handle a product/variant going out of stock between being shown and
  being added to cart, or between recap and final checkout confirmation? → Assistant
  re-checks availability and surfaces the change before mutating anything (User Story 2
  Scenario 5, User Story 3 Scenario 4).
- What happens if a shopper tries to apply an invalid, expired, or already-used promo code?
  → Assistant reports the specific reason via the store's validation response, without
  guessing or fabricating an alternate discount.
- What happens if two promo rules both match the same cart? → Strategy defines
  stackability/priority explicitly; the assistant applies/suggests only what the strategy
  and store allow, and tells the shopper if only one of several eligible codes can be used.
- What happens if the shopper abandons the conversation mid-flow (e.g., after an add-to-cart
  confirmation is shown but before they respond)? → No mutation occurs until confirmation is
  received; the cart remains in its last confirmed state.
- What happens if the shopper asks to check out with an empty cart? → Assistant informs the
  shopper the cart is empty and offers to resume product discovery instead of presenting a
  recap.
- How does the assistant handle a request referencing a product that no longer exists in the
  catalog? → Reports the product is unavailable and offers to search for alternatives.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The assistant MUST interpret natural-language shopping requests (search terms,
  category names, price/attribute constraints) and return matching products from the
  connected store's catalog.
- **FR-002**: The assistant MUST be able to change the shopper's navigation context (e.g.,
  move to a category, search results, or a specific product) based on conversational intent,
  without requiring manual menu/filter interaction.
- **FR-003**: When a shopper's request is ambiguous or underspecified in a way that changes
  scope or outcome, the assistant MUST ask a single, concise clarifying question rather than
  guessing.
- **FR-004**: The assistant MUST support adding a product (with variant and quantity, where
  applicable) to the shopper's cart, but MUST first present a confirmation summarizing the
  intended change and MUST NOT mutate the cart until the shopper explicitly approves.
- **FR-005**: The assistant MUST support updating quantity and removing line items from the
  cart conversationally, following the same confirm-before-mutate rule as FR-004.
- **FR-006**: The assistant MUST integrate with the underlying e-commerce platform (e.g.,
  PrestaShop) through a platform-agnostic interface, so catalog search, cart operations,
  promo validation, and order placement are backed by the real store state (via a
  containerized reference instance for development/testing), not simulated/local-only data.
- **FR-007**: Before checkout, the assistant MUST present a full recap of the cart: every
  line item, quantity, unit price, any applied discount, and the grand total.
- **FR-008**: The assistant MUST place the order in the underlying store only after the
  shopper gives an explicit final confirmation of the recap; it MUST NOT place an order from
  an unconfirmed or stale recap.
- **FR-009**: If cart contents, prices, stock, or applied promos change between the recap
  being shown and the shopper's final confirmation, the assistant MUST re-validate and
  surface the change, requiring a fresh confirmation before proceeding.
- **FR-010**: The assistant MUST be able to proactively suggest a promo code to the shopper
  when the cart matches a defined promo strategy rule (e.g., spend threshold, first order,
  category-specific offer), and MUST clearly explain the benefit of the suggested code.
- **FR-011**: The assistant MUST let the shopper accept or decline any suggested promo code,
  and MUST also accept a promo code the shopper provides manually.
- **FR-012**: The assistant MUST validate any promo code (suggested or shopper-provided)
  against the underlying store before reflecting its discount in the recap or total; it MUST
  NOT claim a discount is applied unless the store confirms the code is valid for the
  current cart.
- **FR-013**: When a promo code is invalid, expired, not eligible, or no promo applies at
  all, the assistant MUST state this plainly rather than inventing or approximating a
  discount.
- **FR-014**: The system MUST log every navigation change, cart mutation, promo
  suggestion/application, and checkout action with enough detail (triggering intent, action
  taken, outcome) to reconstruct the assistant's decisions after the fact.
- **FR-015**: The assistant MUST gracefully handle unavailable/out-of-stock products at any
  point (discovery, add-to-cart, or checkout) by informing the shopper and offering
  alternatives instead of failing silently or mutating the cart with unavailable items.

### Key Entities *(include if feature involves data)*

- **Product**: Catalog item the shopper can discover/navigate to; key attributes include
  name, category, price, variants (e.g., size/color), and stock/availability status, sourced
  from the connected store.
- **Cart**: The shopper's in-progress selection of products for the current session; made up
  of Cart Lines and tracked per shopper session.
- **Cart Line**: A single product (plus variant) and quantity within a Cart, with its own
  unit price and line total.
- **Promo Code**: A discount code with eligibility rules (thresholds, target categories,
  validity window, stackability) defined and ultimately validated by the connected store.
- **Promo Strategy**: The rule set the assistant uses to decide *when* and *which* Promo
  Code to proactively suggest for a given cart/shopper state.
- **Order**: The finalized result of a confirmed checkout; contains the confirmed line
  items, applied discounts, total, and a store-issued order identifier.
- **Conversation Session**: The ongoing shopper interaction; tracks current navigation
  context, cart-in-progress, and any pending (unconfirmed) action awaiting shopper approval.
- **Commerce Adapter Binding**: The connection/configuration linking a Conversation Session
  to a specific underlying e-commerce platform instance (e.g., the reference PrestaShop
  Docker store).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Shoppers can locate and reach a desired product's page (via search/navigation)
  in 3 or fewer conversational turns for at least 80% of common requests.
- **SC-002**: 100% of cart mutations and order placements are preceded by an explicit
  recap/confirmation shown to the shopper — zero silent mutations occur in testing or
  production logs.
- **SC-003**: Shoppers can go from "cart ready" to a placed order (recap shown, confirmed,
  order created) in under 2 minutes for at least 90% of sessions.
- **SC-004**: At least 95% of promo codes the assistant tells the shopper are "applied"
  are confirmed as valid and reflected correctly in the store's own order/cart total (no
  discrepancy between assistant-stated and store-confirmed discount).
- **SC-005**: In sessions where the cart matches at least one configured promo strategy
  rule, the assistant proactively surfaces an applicable code at least 90% of the time.
- **SC-006**: At least 90% of shoppers who reach the checkout recap either confirm or make an
  edit-then-confirm within the same session (i.e., the recap step does not cause abandonment
  due to confusion or mistrust in usability testing).

## Assumptions

- The reference/development e-commerce backend is PrestaShop, run as an official Docker
  image (or docker-compose stack); the design generalizes to any platform reachable through
  the same commerce adapter interface, but PrestaShop is the concrete integration target for
  this feature.
- A single storefront, single currency, and single locale are in scope for this feature;
  multi-store/multi-currency support is out of scope.
- Shopping sessions may be guest (unauthenticated) or tied to an existing store account;
  this feature reuses whatever identity/session mechanism the underlying store already
  provides rather than introducing a new one.
- Promo codes themselves are configured/managed in the underlying store (or a companion
  admin process) — this feature's assistant decides *when/what to suggest* and *validates*
  codes, but does not invent new codes or discount amounts on its own.
- Payment processing/capture is handled by the underlying store's existing checkout
  mechanism; this feature is responsible for driving the shopper to a confirmed order, not
  for implementing a new payment gateway.
- Standard web-assistant expectations apply for performance and error messaging where not
  explicitly specified (e.g., friendly fallback messages, retry-once semantics on
  transient integration errors).
