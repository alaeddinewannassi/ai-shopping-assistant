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

**Do you need to pay for an LLM? No.** This project is scoped as an internship/demo
deliverable, so the default is a **free-tier** option that still gives realistic
conversation. Pick a provider via `LLM_PROVIDER` in `.env` (see research.md §3a):

- `LLM_PROVIDER=free-tier-hosted` (**recommended default, free**) — a hosted tool-calling
  API with a genuinely free tier, e.g. [Groq](https://console.groq.com) (Llama 3.1/3.3,
  fast, generous free daily requests — roughly ~14,000 requests/day, good for ~1,000+ full
  demo conversations/day, see research.md §3a) or Google's Gemini API free tier (tighter,
  roughly ~250-1,500 requests/day depending on model — still plenty for dev + a live demo).
  Sign up, grab a free `LLM_API_KEY`, no billing/credit card needed for free-tier usage.
  This is what you'd use to actually demo the assistant with realistic, open-ended
  conversation. Exact quotas change over time — check the provider's live limits page
  before relying on a number.
- `LLM_PROVIDER=rule-based-stub` (**free**, used for automated tests) — deterministic
  keyword matcher; runs the full test suite (T017a) and mechanically validates every
  acceptance scenario in `spec.md` instantly, with zero external dependency. Not meant to
  carry a live demo conversation, only automated tests/CI.
- `LLM_PROVIDER=hosted-paid` (**paid**, optional) — only relevant if this ever grows beyond
  the internship into real production traffic; not needed to complete this deliverable.

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

### Resilience — store backend unreachable (FR-016, research.md §8)

Simulate an outage mid-session and confirm the assistant degrades honestly instead of
fabricating data or silently mutating anything:

```bash
# 1. Do a normal search first so a Catalog Snapshot gets cached, e.g.:
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"show me jackets"}'

# 2. Simulate the store going down:
docker compose stop prestashop

# 3. Try another read (discovery/navigation):
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"show me shoes under $50"}'
```
Expect: either a result served from the cached Catalog Snapshot with an explicit "this may
be outdated" disclaimer, or — if nothing relevant was cached — a plain message that search is
temporarily unavailable. Either way, no fabricated products.

```bash
# 4. Try a mutation (add to cart) while still down:
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"add the blue jacket, size M, to my cart"}'
```
Expect: a plain refusal message stating the change can't be verified/applied right now — no
recap/confirmation prompt is shown, no `PendingAction` is created, and the cart is unchanged.

```bash
# 5. Restore the store and confirm normal operation resumes:
docker compose start prestashop
curl -s localhost:8000/chat -d '{"session_id":"demo","message":"add the blue jacket, size M, to my cart"}'
```
Expect: normal recap/confirmation flow resumes once the adapter can reach the store again.

## 5a. Demo-day reliability checklist (LLM provider network risk)

`free-tier-hosted` is the only supported live LLM path for this project (see research.md
§3a — local/Ollama was explicitly evaluated and rejected for this scope: tool-calling
reliability on small quantized models and the intern's limited engineering time outweigh
the RAM savings, per an adversarial design review). Since every conversational turn needs
this API reachable, mitigate the "no internet at demo time" risk cheaply instead of adding a
second LLM backend:

- Test the venue's Wi‑Fi and the actual Groq/Gemini endpoint reachability (`curl` a
  lightweight request) at least the day before and again right before the demo.
- Have a mobile hotspot as a backup network, tested in advance.
- Keep `rule-based-stub` (T017a) as a rehearsed manual fallback: if the live API is
  unreachable, you can still demo the deterministic scripted flow end-to-end (no realistic
  free-form conversation, but every acceptance scenario in `spec.md` still runs).
- Check the provider's live rate-limit dashboard shortly before the demo, and avoid heavy
  ad-hoc testing against the same API key in the hour before presenting.

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
