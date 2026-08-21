# Quickstart: AI Shopping Assistant Reference Environment

This guide brings up the full reference stack (PrestaShop + MySQL + Redis + assistant
service) locally so the acceptance scenarios in `spec.md` can be validated end-to-end.

## 1. Prerequisites

- Docker + Docker Compose
- (For local, non-container development of the assistant service) Python 3.11+

## 2. Bring up the reference store

```bash
cd docker
docker compose up -d prestashop mysql redis
```

Wait for PrestaShop's first-run install to complete (check `docker compose logs -f
prestashop` until it reports ready), then:

1. Complete the PrestaShop install wizard (or use the pre-baked fixture DB, if provided in
   `docker/prestashop/`) with a small demo catalog (a handful of products across 2+
   categories, at least one with variants, at least one deliberately out-of-stock).
2. In PrestaShop Admin → Webservice, enable the Webservice API and create an API key with
   access to: products, categories, carts, cart_rules, orders.
3. In PrestaShop Admin → Cart Rules, create at least two demo promo codes matching the
   `PromoStrategy` rules used in tests, e.g.:
   - `WELCOME10` — 10% off, first-order only
   - `BIGCART15` — 15% off, subtotal >= 100

## 3. Configure the assistant service

```bash
cp backend/.env.example backend/.env
# set PRESTASHOP_BASE_URL, PRESTASHOP_API_KEY (from step 2), REDIS_URL
```

## 4. Run the assistant service

```bash
docker compose up -d assistant-service
# or, for local dev without rebuilding the image:
cd backend && uvicorn src.api.chat:app --reload
```

## 5. Validate each user story

### US1 — Conversational Product Discovery & Navigation

```bash
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"show me jackets under $100"}'
```
Expect: a list of matching demo products, filtered by category/price, no cart mutation, no
confirmation prompt (read-only).

### US2 — Add to Cart with Confirmation

```bash
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"add the blue jacket, size M, to my cart"}'
```
Expect: a recap/confirmation response (product, variant, qty, price) — verify via
`docker compose exec mysql ...` or the PrestaShop admin cart view that **no line was added
yet**. Then:

```bash
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"yes, add it"}'
```
Expect: confirmation the item was added; verify the cart now shows the line in PrestaShop.

### US3 — Checkout with Full Recap & Final Confirmation

```bash
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"checkout"}'
```
Expect: a full recap (items, qty, unit price, discounts, total). Verify **no order exists
yet** in PrestaShop Admin → Orders. Then:

```bash
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"yes, place the order"}'
```
Expect: an order confirmation with an order id; verify the order now appears in PrestaShop
Admin → Orders with matching totals.

### US4 — Strategic Promo Code Suggestions

Add enough items to exceed the `BIGCART15` threshold, then send any chat message (e.g.,
"what else do you have?") and expect the assistant to proactively mention `BIGCART15`.
Confirm applying it, then verify in the recap/PrestaShop cart that the discount total matches
what PrestaShop's own cart-rule computes (not an assistant-invented number). Also try:

```bash
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"apply code FAKE123"}'
```
Expect: the assistant reports the code is invalid (via `validate_promo`), not a fabricated
discount.

## 6. Run the automated checks

```bash
cd backend
pytest tests/unit tests/contract tests/integration
```

`tests/contract/` requires the Docker reference store to be up (step 2); `tests/unit/` does
not.

## 7. Tear down

```bash
cd docker && docker compose down -v
```
