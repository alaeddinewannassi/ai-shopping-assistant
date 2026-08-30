# Full-journey E2E test

A Playwright suite that drives two complete, natural-language shopping conversations on
**two different tenants** ("shopping websites") through the real `chatbot/widget` — backed
by a real Groq model, not the `rule-based-stub` every other test in this repo uses — then
verifies each tenant's admin sees *only* their own store's analytics in
`backoffice/frontend`. See `specs/002-backoffice-analytics/plan.md` for the feature this
proves end-to-end.

## What it spans

This is the one place in the repo that exercises `chatbot/` and `backoffice/` together,
across all four services:

| Service | Port | Role |
|---|---|---|
| `chatbot/backend` | 8000 | Real Groq-backed conversations, two tenants |
| static file server (`widget-demo/`) | 4000 | Hosts the real, built widget bundle |
| `backoffice/backend` | 8001 | Reads the same event stream the conversations wrote |
| `backoffice/frontend` | 5173 | Dashboards verified per-tenant against those conversations |

All four point at one throwaway SQLite database (`.tmp/e2e.db`), wiped and reseeded fresh
before every run.

## Setup

Both backend venvs must already exist with `tenancy-db` installed (see the root
[`README.md`](../README.md) and [`backoffice/README.md`](../backoffice/README.md) if not):

```bash
npm install
npx playwright install chromium   # once, if not already cached
```

## Running

```bash
# Smoke tests only (no LLM key needed — proves the harness itself: seeding both tenants,
# all four servers, tenant resolution via each tenant's own widget key, backoffice
# reachability):
npm test -- tests/smoke.spec.ts

# The real, full journey — requires a real Groq API key in YOUR OWN shell:
LLM_API_KEY=your-real-groq-key npm test -- tests/multi-tenant-journey.spec.ts

# Watch it happen: character-by-character typing + multi-second pauses at every step,
# instead of finishing in well under 30 seconds:
E2E_SLOW=1 LLM_API_KEY=your-real-groq-key npm test -- tests/multi-tenant-journey.spec.ts --headed

# Everything:
LLM_API_KEY=your-real-groq-key npm test
```

**Never paste a real API key into a chat/AI assistant session** — set it directly in your
own terminal. Without `LLM_API_KEY` set, `multi-tenant-journey.spec.ts` reports itself
**skipped**, not failed — exactly like `chatbot/backend/tests/contract/
test_adapter_contract_prestashop.py` does when no live PrestaShop store is configured. Every
other test in the repo is unaffected either way.

`E2E_SLOW=1` must be a real shell env var set *before* `npm test` runs — it can't be
auto-detected from `--headed` inside the test file. Playwright's test-execution worker is a
separate process from the one that evaluates `playwright.config.ts`, and doesn't reliably
inherit either the original CLI argv or an in-process `process.env` mutation made after that
worker was already spawned (this was tried and empirically failed — see
`playwright.config.ts`'s `E2E_SLOW` comment). A plain shell env var has no such timing
dependency.

`npm test` first runs `pretest` (`build:widget` + `seed`) — see `scripts/seed-db.mjs`'s own
header comment for why seeding happens there, as a plain script *before* `playwright test`
is even invoked, rather than as Playwright's own `globalSetup`.

## What the journey actually does

`tests/multi-tenant-journey.spec.ts` runs 5 tests, in order, in one worker
(`test.describe.serial` — the backoffice checks read data the store tests just created):

**Store One** — a full purchase, all via natural free-text typed into the widget:
1. Discovery — *"I'm looking for a comfortable t-shirt in red"*
2. Navigate to a category — *"show me the jackets category"*
3. Add to cart — *"add the red classic t-shirt to my cart please"*, then confirm
4. Change the quantity — *"update the t-shirt quantity to 2"*, then confirm
5. Ask about discounts — the seeded `welcome10` rule (first order) fires, then confirm
6. Checkout — *"I'd like to check out now"*, then confirm → *"Order placed!"*

**Store Two** — a genuinely different outcome: browses, changes their mind, then abandons:
1. Discovery — *"I'm looking for a blue jacket"*
2. Propose adding it, then **decline** — *"no, never mind"*
3. Propose it again, confirm this time
4. Remove it from the cart, confirm — cart ends up empty, no checkout ever attempted

Between the two stores, every one of the fixed 9 action types
(`chatbot/backend/src/agent/llm_client.py`'s tool schema) gets exercised at least once:
`search_products`, `navigate_to`, `propose_add_to_cart`, `propose_update_cart`,
`propose_remove_from_cart`, `apply_promo`, `request_checkout`, `confirm_pending_action`,
`decline_pending_action`.

**Backoffice, twice for isolation** — once per tenant, each logging in as that tenant's own
scoped `owner` admin (not a shared superadmin, which would see everything by design and so
wouldn't actually prove isolation): opens **Sessions** and confirms it sees its own
session and *not* the other tenant's, replays the event stream, then checks **Overview**
and **Funnel** — all asserted as *exact* numbers (Store One: 100% conversion, ordered;
Store Two: 0% conversion, never ordered), since this fresh database has exactly one
session per tenant.

**Backoffice, once more for the complementary story** — real multi-tenancy isn't only
"each store's admin is isolated," it's also "one backoffice login can legitimately manage
several websites." The 5th test logs in as a THIRD seeded admin
(`e2e-multi-admin@example.com`, seeded in `e2e/seed.py`'s `_seed_multi_tenant_admin` with an
`analyst` membership on *both* tenants) and uses `AppShell.tsx`'s tenant switcher to view
Store One's sessions, then switches — same login, no re-authentication — and confirms the
view flips entirely to Store Two's. This also exercises `MembershipOut` returning each
tenant's real name (not just its id), since the switcher renders `E2E Store One` / `E2E
Store Two`, not truncated UUIDs.

## Commerce backend: mock, not live PrestaShop

Both seeded tenants use `platform="mock"` (`MockAdapter`, the same in-memory catalog every
other test in the repo already exercises — "Classic T-Shirt" red/blue $19.99, "Blue Jacket"
sizes M/L $89.99). This suite is about conversational NLU + the confirm-gate +
multi-tenant analytics, which a live PrestaShop store would add unrelated risk and setup
weight to without adding coverage — that's what
`chatbot/backend/tests/contract/test_adapter_contract_prestashop.py` is for, separately.

## Known gaps

- No CI wiring — this suite is meant to be run locally with a real key when you want to
  verify the whole system, not on every commit.
- `reuseExistingServer` is hardcoded `false` for all four servers (see
  `playwright.config.ts`'s comment) — every run pays full server-startup cost, trading the
  usual local-dev speedup for guaranteed-fresh state.
- A stale venv shebang (this repo's venvs were created under a since-renamed directory) is
  worked around by invoking `python -m uvicorn` rather than the `uvicorn` console script —
  if you ever recreate `chatbot/backend/.venv` or `backoffice/backend/.venv` from scratch,
  this workaround becomes unnecessary but stays harmless.
- The Blue Jacket's size must be named explicitly in the message (`"...in size M..."`) —
  it has two size variants, and the system's ambiguity handling (research.md §9.4) asks a
  clarifying question rather than guess, but has no memory of that reply to interpret a
  bare follow-up like "the medium one" against (every turn re-resolves from scratch); a real
  shopper re-states the full request with the missing detail, same as this test does.
