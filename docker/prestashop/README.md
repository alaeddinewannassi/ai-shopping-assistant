# PrestaShop demo fixtures (T006)

`docker compose up -d prestashop mysql redis` (from `docker/`) brings up a stock PrestaShop
8 install with `PS_INSTALL_AUTO=1`, so the install wizard runs unattended using PrestaShop's
own default demo catalog. That default catalog already satisfies most of quickstart.md's
fixture requirements (multiple categories, products with variants), but two things need to
be set up by hand afterward because they aren't part of a stock install: the Webservice API
key, and the demo promo codes this project's tests/quickstart walkthrough expect.

Do these once, in PrestaShop Admin (`http://localhost:8080/admin`, login from
`PS_ADMIN_EMAIL`/`PS_ADMIN_PASSWORD` in `docker/.env`, defaulting to
`demo@example.com` / `AssistantDemo123!` per `docker-compose.yml`):

## 1. Enable the Webservice API

Admin → **Advanced Parameters → Webservice**:

1. Turn Webservice **on**.
2. **Add a new webservice key.** Grant it GET/PUT/POST/DELETE on: `products`, `categories`,
   `combinations`, `product_options`, `product_option_values`, `stock_availables`, `carts`,
   `cart_rules`, `orders`, `specific_prices` (the last one is easy to miss — it's how a
   validated promo code's discount actually gets applied to a cart; without it,
   `validate_promo` succeeds but `apply_promo` fails with a 403).
3. Copy the generated key into `backend/.env` as `PRESTASHOP_API_KEY`.

## 2. Confirm/adjust the demo catalog

The stock install ships a small catalog already split across categories, with at least one
product carrying Size/Color combinations. Check under **Catalog → Products** that:

- At least 2 categories exist with products in them.
- At least one product has combinations (variants) — the stock demo hoodie/t-shirt usually
  does.
- At least one variant is deliberately set out of stock (**Catalog → Products → \[a
  product\] → Quantities** → set a combination's quantity to 0 and disable "allow
  backorders") — spec.md's out-of-stock scenarios need this.

## 3. Create the demo promo codes (Cart Rules)

Admin → **Catalog → Discounts → Cart Rules → Add new cart rule**, twice:

| Code | Discount | Condition | Notes |
|---|---|---|---|
| `WELCOME10` | 10% off | none (first-order framing is enforced by the assistant's session state, not the store) | Set **Restrictions → Total available** to a high number, no minimum amount |
| `BIGCART15` | 15% off | Minimum cart amount ≥ 100 | Set **Conditions → Minimum amount** to 100 |

Both must be **active**, with a valid (or empty/open-ended) date range, and not restricted to
a single customer — these are matched against `src/promo/rules.json`'s
`welcome-first-order`/`big-cart` rules (T056).

## 4. Create the checkout prerequisites

PrestaShop's webservice has no anonymous-checkout shortcut — creating an order needs an
existing customer, address, and carrier id. Create one of each (**Customers → Add new**,
then an address for them, and **Shipping → Carriers** — the stock install ships at least one
enabled carrier) and put their ids in `backend/.env`:

```
PRESTASHOP_DEFAULT_CUSTOMER_ID=<id from Admin → Customers>
PRESTASHOP_DEFAULT_ADDRESS_ID=<id from that customer's Addresses tab>
PRESTASHOP_DEFAULT_CARRIER_ID=<id from Shipping → Carriers>
```

## 5. Verify

```bash
cd backend
cp .env.example .env   # then fill in the values from steps 1-4
source .venv/bin/activate
pytest tests/contract/test_adapter_contract_prestashop.py -v
```

All cases should pass (not skip) once `PRESTASHOP_BASE_URL`/`PRESTASHOP_API_KEY` are set and
the store is reachable.
