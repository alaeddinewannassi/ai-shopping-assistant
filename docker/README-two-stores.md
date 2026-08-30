# Manual test: two live PrestaShop stores, one backoffice

This brings up two independent, real PrestaShop storefronts — each with the actual chat
widget baked into its footer (`docker/prestashop/Dockerfile`) — served by one chatbot
backend and administered from one backoffice login. It's the hands-on version of what
`e2e/tests/multi-tenant-journey.spec.ts` already proves automatically: isolation between
two tenants, and one login legitimately managing both.

## What's running

| Service | URL | Role |
|---|---|---|
| Store One (PrestaShop) | http://localhost:8080 | Real storefront, widget already embedded |
| Store One admin | http://localhost:8080/admin | Where you generate its Webservice key |
| Store Two (PrestaShop) | http://localhost:8090 | Second, independent storefront |
| Store Two admin | http://localhost:8090/admin | Same, for store two |
| Chatbot backend | http://localhost:8000 | One process, serves both tenants by `X-Assistant-Key` |
| Backoffice API | http://localhost:8001 | Analytics/admin API |
| Backoffice dashboard | http://localhost:5173 | Where you log in once to see both stores |
| `migrate` | (none — one-shot) | Applies tenancy-db's Alembic migrations, then exits |

## 1. Bring up the stack

```bash
cd docker
docker compose up -d --build
```

First run builds both PrestaShop images (each does an `npm run build` of `chatbot/widget`
internally, then bakes the bundle + a `<assistant-chat-widget>` tag into
`themes/classic/templates/_partials/footer.tpl`) and runs PrestaShop's own unattended
installer against two separate MySQL databases. Give it a few minutes; watch progress with
`docker compose ps` (once `prestashop`/`prestashop-two` are running, http://localhost:8080
and :8090 answer).

`assistant-service`/`backoffice-service` both wait on the one-shot `migrate` service
(`tenancy-db/Dockerfile`, `condition: service_completed_successfully`) to apply the schema
to Postgres before they start — nothing to do here, but if either service fails to boot,
`docker compose logs migrate` is the first place to check.

If you already ran `docker compose up` **before** this two-store setup existed, the named
volumes (`prestashop_data`/`prestashop_data_two`) were seeded from the old single-store
image and won't pick up the widget — run `docker compose down -v` once first (this wipes
both stores' catalogs back to the stock demo data) and re-run the command above.

## 2. Configure each store by hand (once per store)

This part can't be automated — it's you, in each store's own Admin, doing the same steps
`docker/prestashop/README.md` already documents for one store. Do it twice, once at
`localhost:8080/admin`, once at `localhost:8090/admin` (both log in with
`demo@example.com` / `AssistantDemo123!` and `demo-two@example.com` / `AssistantDemo123!`
respectively, per `docker-compose.yml`'s defaults):

1. **Advanced Parameters → Webservice** → turn it on, add a key with GET/PUT/POST/DELETE on
   `products`, `categories`, `combinations`, `product_options`, `product_option_values`,
   `stock_availables`, `carts`, `cart_rules`, `orders`. Copy the key.
2. **Customers → Add new** → create one, then add an address for them. Note both ids.
3. **Shipping → Carriers** → note an enabled carrier's id (the stock install ships one).
4. Optional, for the promo-suggestion feature: **Catalog → Discounts → Cart Rules**, create
   `WELCOME10` (10% off, no minimum) and `BIGCART15` (15% off, min. cart amount 100) — both
   active, unrestricted to a single customer. `provision_two_stores.py` below seeds the
   assistant's *own* matching rule definitions from `chatbot/backend/src/promo/rules.json`
   automatically; these PrestaShop-side cart rules are what actually gets applied to an
   order.

You'll end up with, per store: a webservice API key, a customer id, an address id, a
carrier id.

## 3. Provision both tenants + one shared admin login

Needs the backoffice backend's venv (already has `tenancy_db` installed) and a running
Postgres (`docker compose up -d postgres` if you haven't already, or reuse the one this
stack just started).

```bash
cd backoffice/backend
source .venv/bin/activate   # if this venv doesn't exist yet, see backoffice/README.md's setup

export DATABASE_URL=postgresql+psycopg://assistant:assistant@localhost:5432/assistant
export APP_ENCRYPTION_KEY=<same value in backoffice/backend/.env and chatbot/backend/.env>
export LLM_API_KEY=<your real Groq key — never paste it into a chat/AI session>

python ../../docker/provision_two_stores.py \
  --admin-email you@example.com --admin-password 'change-me' \
  --store-one-api-key <webservice key from store one> \
  --store-one-customer-id <id> --store-one-address-id <id> --store-one-carrier-id <id> \
  --store-two-api-key <webservice key from store two> \
  --store-two-customer-id <id> --store-two-address-id <id> --store-two-carrier-id <id>
```

This creates `demo-store-one`/`demo-store-two` tenants pointing at the two live
PrestaShop instances, issues each the SAME fixed widget key
(`demo-widget-key-store-one`/`-two`) already baked into that store's footer, and creates
one admin — `you@example.com` — with `owner` membership on **both**. Re-running is safe
(e.g. after rotating a webservice key).

## 4. Chat with each real storefront

Open **http://localhost:8080** and **http://localhost:8090** in two tabs — each is a real
PrestaShop storefront with the chat launcher in the bottom-right corner already, no widget
demo page needed. Try a few turns in each (discovery, add to cart with confirmation,
checkout) — each conversation writes analytics events tagged to that store's own tenant.

Reminder from `chatbot/backend/src/agent/intents.py`'s `handle_navigate`: asking the
assistant to "show me the jackets category" returns that category's products as a **chat
reply**, not a real page navigation — the widget never does `window.location`. Chatting
and browsing the actual storefront pages are two separate things you're testing side by
side, not one driving the other.

## 5. Verify in the backoffice — one login, both stores

Open **http://localhost:5173**, log in as `you@example.com` / the password you set in step
3. The sidebar shows a tenant switcher (only appears once an account has more than one
membership) listing **Demo Store One** and **Demo Store Two** by name. Pick one, check
**Sessions**/**Overview**/**Funnel** for the conversation you just had there, then switch
to the other — same login, no re-authentication — and confirm the dashboard shows that
store's own session, not the first one's.

## Troubleshooting

- **500 error mentioning `_dialogue_ctx` or `NotImplementedError` in `llm_client.py`**:
  `assistant-service`/`backoffice-service` are running a stale image built before this
  repo's `backend/` → `chatbot/backend/` restructuring (Docker only rebuilds on `--build`,
  never automatically). Fix: `docker compose up -d --build assistant-service
  backoffice-service backoffice-frontend`.
- **Widget doesn't appear on the storefront**: check you rebuilt after this Dockerfile
  landed (`docker compose up -d --build`) and that the volume wasn't pre-seeded from the
  old image (`docker compose down -v` once, see step 1).
- **Chat replies with a generic "couldn't reach the assistant" message**: open browser
  devtools → Network, confirm the request goes to `http://localhost:8000` and carries an
  `X-Assistant-Key` header with the store's widget key — if it's missing, the footer
  injection didn't take (rebuild that store's image).
- **Tenant switcher doesn't show up in the backoffice**: it only renders when
  `user.memberships.length > 1` (`backoffice/frontend/src/components/AppShell.tsx`) —
  confirm `provision_two_stores.py` actually ran against the same `DATABASE_URL` the
  backoffice backend is using, and that you logged in with the exact email you passed it.
