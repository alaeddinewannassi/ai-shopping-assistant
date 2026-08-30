"""PrestaShopAdapter: real-store CommerceAdapter implementation (T012).

Talks to PrestaShop's Webservice REST API (research.md §1) over httpx. Two format quirks
drive the shape of this module:

  1. GET responses are requested as JSON (`output_format=JSON`), but as of PrestaShop 8.1
     the webservice can only *read* XML request bodies — every mutating call (POST/PUT/
     PATCH) must send a small `<prestashop>...</prestashop>` XML envelope, even though the
     response comes back as JSON. See `_build_xml` / `_xml_request`.
  2. Multi-language fields (`name`, `description`, ...) come back as a list of
     `{"id": <lang_id>, "value": <text>}` entries, not a flat string. See `_localized`.

IMPORTANT — not integration-tested against a live store in this session (no PrestaShop
Docker container was reachable while this was written; see quickstart.md). The contract
test suite (`tests/contract/test_adapter_contract.py`) is parametrized to also run against
this adapter and will skip automatically if `PRESTASHOP_BASE_URL`/`PRESTASHOP_API_KEY` don't
point at a reachable store — run `docker compose up` (docker/docker-compose.yml) and rerun
the contract tests before trusting this against a real deployment. The highest-risk areas
are combination/attribute resolution (`_load_variants`) and order creation (`checkout`),
which requires a pre-configured demo customer/address/carrier (see .env.example) because
PrestaShop's own webservice requires those ids up front — there is no anonymous/guest
checkout shortcut at the webservice layer.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from src.adapters.base import (
    AdapterUnavailableError,
    AttributeGroup,
    Cart,
    CartLine,
    CartStateChangedError,
    Category,
    Order,
    OutOfStockError,
    Product,
    ProductNotFoundError,
    PromoInvalidError,
    PromoValidation,
    Variant,
)
from src.adapters.resilience import CircuitBreaker, CircuitBreakerConfig, default_is_transport_error

_NO_COMBINATION_ATTR_ID = 0  # PrestaShop convention: id_product_attribute=0 means "the product itself"


class _TransportError(Exception):
    """Internal marker: a genuine transport/timeout/5xx failure, reclassified into
    AdapterUnavailableError by the CircuitBreaker — never a business error."""


def _is_transport_error(exc: Exception) -> bool:
    return default_is_transport_error(exc) or isinstance(
        exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, _TransportError)
    )


def _localized(value: Any, lang_id: int) -> str:
    """Unwraps a PrestaShop multi-language field (`[{"id": "1", "value": "..."}]`) to a
    plain string for the configured language, tolerating a flat string too."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "language" in value:
        value = value["language"]
    if isinstance(value, list):
        for entry in value:
            if str(entry.get("id")) == str(lang_id):
                return entry.get("value", "") or ""
        if value:
            return value[0].get("value", "") or ""
    return ""


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    return str(value) in ("1", "true", "True")


def _split_variant_id(variant_id: str) -> tuple[int, int]:
    product_id_str, _, attr_id_str = variant_id.partition("#")
    return _as_int(product_id_str), _as_int(attr_id_str)


class PrestaShopAdapter:
    """Real-store CommerceAdapter implementation backed by PrestaShop's Webservice API.

    Variant ids are `"{id_product}#{id_product_attribute}"` (id_product_attribute=0 for a
    product with no combinations, per PrestaShop's own stock_availables convention) so a
    single string round-trips back to both ids without a lookup table.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        lang_id: int | None = None,
        timeout_seconds: float | None = None,
        host_header: str | None = None,
        default_customer_id: str | None = None,
        default_address_id: str | None = None,
        default_carrier_id: str | None = None,
        default_currency_id: str | None = None,
        default_order_state_id: str | None = None,
        payment_module: str | None = None,
        payment_label: str | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("PRESTASHOP_BASE_URL", "")).rstrip("/")
        # Every call site below passes a path already prefixed with "/api/..." (matching
        # PrestaShop's webservice docs), but PRESTASHOP_BASE_URL is documented (.env.example,
        # quickstart.md) to itself end in "/api" — strip that suffix so the two don't combine
        # into a double "/api/api/..." path that 404s/400s on every real request.
        self._base_url = self._base_url.removesuffix("/api")
        self._api_key = api_key or os.environ.get("PRESTASHOP_API_KEY", "")
        if not self._base_url or not self._api_key:
            raise ValueError(
                "PrestaShopAdapter requires PRESTASHOP_BASE_URL and PRESTASHOP_API_KEY "
                "(backend/.env.example)."
            )
        self._lang_id = lang_id or _as_int(os.environ.get("PRESTASHOP_LANG_ID", "1"), 1)

        timeout = timeout_seconds or _as_float(os.environ.get("ADAPTER_TIMEOUT_SECONDS", "5"), 5.0)
        # PrestaShop's front controller redirects to its configured PS_DOMAIN whenever the
        # request Host header doesn't match it (see quickstart.md's dockerized-store note) —
        # this bites the common case of reaching the store through a Docker-internal hostname
        # (e.g. PRESTASHOP_BASE_URL=http://prestashop/api) that differs from the browser-facing
        # PS_DOMAIN. Overriding the Host header to PS_DOMAIN sidesteps the redirect without
        # touching PrestaShop's own domain config.
        client_headers = {}
        host_header = host_header or os.environ.get("PRESTASHOP_HOST_HEADER", "")
        if host_header:
            client_headers["Host"] = host_header
        self._client = httpx.Client(auth=(self._api_key, ""), timeout=timeout, headers=client_headers)
        self._breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=_as_int(os.environ.get("ADAPTER_FAILURE_THRESHOLD", "3"), 3),
                recovery_seconds=_as_float(os.environ.get("ADAPTER_RECOVERY_SECONDS", "30"), 30.0),
                timeout_seconds=timeout,
            )
        )

        # Checkout requires a pre-existing demo customer/address/carrier/payment module —
        # PrestaShop's webservice has no anonymous-checkout shortcut (see module docstring).
        self._customer_id = default_customer_id or os.environ.get("PRESTASHOP_DEFAULT_CUSTOMER_ID")
        self._address_id = default_address_id or os.environ.get("PRESTASHOP_DEFAULT_ADDRESS_ID")
        self._carrier_id = default_carrier_id or os.environ.get("PRESTASHOP_DEFAULT_CARRIER_ID")
        self._currency_id = default_currency_id or os.environ.get("PRESTASHOP_DEFAULT_CURRENCY_ID", "1")
        self._order_state_id = default_order_state_id or os.environ.get(
            "PRESTASHOP_DEFAULT_ORDER_STATE_ID", "1"
        )
        self._payment_module = payment_module or os.environ.get(
            "PRESTASHOP_PAYMENT_MODULE", "ps_wirepayment"
        )
        self._payment_label = payment_label or os.environ.get("PRESTASHOP_PAYMENT_LABEL", "Bank wire")

        # Maps the assistant's opaque session/cart_id string onto a real PrestaShop id_cart,
        # created lazily on first use (mirrors MockAdapter keying carts by that same string).
        self._cart_id_map: dict[str, int] = {}
        self._customer_secure_key_cache: str | None = None
        # PrestaShop's webservice has no association to attach a cart_rule to a cart or
        # order (the `cart`/`order` resource schemas only expose *_rows, never cart_rules —
        # confirmed via GET .../carts?schema=synopsis), so an applied promo code's *name*
        # can't be persisted on the store's side the way cart lines are — tracked here
        # instead, purely so get_cart() can report it back (see apply_promo/_read_cart).
        # The actual discount itself is applied via a cart-scoped `specific_price` (see
        # apply_promo/_apply_specific_price_discount), which PrestaShop's own order-total
        # calculation does honor — confirmed empirically against a real order.
        self._applied_promo: dict[int, str] = {}

    # -- HTTP plumbing ------------------------------------------------------- #

    def _get(self, path: str, params: dict | None = None) -> Any:
        params = dict(params or {})
        params["output_format"] = "JSON"

        def do() -> httpx.Response:
            try:
                resp = self._client.get(f"{self._base_url}{path}", params=params)
            except httpx.HTTPError as exc:
                raise _TransportError(str(exc)) from exc
            if resp.status_code >= 500:
                raise _TransportError(f"PrestaShop returned {resp.status_code} for GET {path}")
            return resp

        resp = self._breaker.call(do, is_transport_error=_is_transport_error)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise _TransportError(f"PrestaShop returned {resp.status_code} for GET {path}: {resp.text[:200]}")
        return resp.json()

    def _xml_request(self, method: str, path: str, xml_body: str) -> Any:
        def do() -> httpx.Response:
            try:
                resp = self._client.request(
                    method,
                    f"{self._base_url}{path}",
                    params={"output_format": "JSON"},
                    content=xml_body.encode("utf-8"),
                    headers={"Content-Type": "text/xml"},
                )
            except httpx.HTTPError as exc:
                raise _TransportError(str(exc)) from exc
            if resp.status_code >= 500:
                raise _TransportError(f"PrestaShop returned {resp.status_code} for {method} {path}")
            return resp

        return self._breaker.call(do, is_transport_error=_is_transport_error)

    @staticmethod
    def _build_xml(resource: str, fields: dict[str, Any], associations_xml: str = "") -> str:
        parts = [f"<{resource}>"]
        for key, value in fields.items():
            parts.append(f"<{key}><![CDATA[{value}]]></{key}>")
        if associations_xml:
            parts.append(f"<associations>{associations_xml}</associations>")
        parts.append(f"</{resource}>")
        body = "".join(parts)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">' + body + "</prestashop>"
        )

    # -- Read-only: discovery/navigation ------------------------------------ #

    def search_products(self, query: str = "", filters: dict | None = None) -> list[Product]:
        filters = filters or {}
        params: dict[str, Any] = {"display": "full", "filter[active]": "1"}
        category_id = filters.get("category_id")
        if category_id:
            params["filter[id_category_default]"] = _as_int(category_id)

        data = self._get("/api/products", params)
        raw_products = self._as_list(data, "products", "product")

        products = [self._map_product(p) for p in raw_products]

        if query:
            tokens = [t for t in query.lower().split() if len(t) > 2]
            if tokens:
                # A plain `t in name` substring check missed simple singular/plural
                # mismatches ("posters" vs. a catalog name containing "poster") — mirrors
                # MockAdapter.search_products' _fold()/bidirectional per-word matching so
                # both adapters behave consistently against the same query.
                def _fold(word: str) -> str:
                    return word[:-1] if word.endswith("s") and len(word) > 3 else word

                folded_tokens = [_fold(t) for t in tokens]
                products = [
                    p for p in products
                    if any(
                        _fold(t) in name_token or name_token in _fold(t)
                        for t in folded_tokens
                        for name_token in p.name.lower().replace("-", " ").split()
                        if len(name_token) > 2
                    )
                ]

        color = filters.get("color")
        if color:
            products = [
                p for p in products
                if any(v.attributes.get("color", "").lower() == color.lower() for v in p.variants)
            ]
        max_price = filters.get("max_price")
        if max_price is not None:
            products = [p for p in products if p.base_price <= max_price]

        return products

    def get_product(self, product_id: str) -> Product:
        data = self._get(f"/api/products/{_as_int(product_id)}")
        if data is None:
            raise ProductNotFoundError(f"No such product: {product_id}")
        raw = data.get("product", data)
        return self._map_product(raw)

    def list_categories(self) -> list[Category]:
        data = self._get("/api/categories", {"display": "full", "filter[active]": "1"})
        raw = self._as_list(data, "categories", "category")
        return [
            Category(
                id=str(c["id"]),
                name=_localized(c.get("name"), self._lang_id),
                parent_id=str(c["id_parent"]) if _as_int(c.get("id_parent")) else None,
            )
            for c in raw
        ]

    def list_attributes(self) -> list[AttributeGroup]:
        groups_data = self._get("/api/product_options", {"display": "full"})
        groups_raw = self._as_list(groups_data, "product_options", "product_option")

        values_data = self._get("/api/product_option_values", {"display": "full"})
        values_raw = self._as_list(values_data, "product_option_values", "product_option_value")

        groups: list[AttributeGroup] = []
        for g in groups_raw:
            group_id = str(g["id"])
            name = _localized(g.get("name"), self._lang_id) or g.get("public_name", "")
            values = [
                _localized(v.get("name"), self._lang_id)
                for v in values_raw
                if str(v.get("id_attribute_group")) == group_id
            ]
            groups.append(AttributeGroup(name=name, values=[v for v in values if v]))
        return groups

    def get_cart(self, session_id: str) -> Cart:
        id_cart = self._cart_id_map.get(session_id)
        if id_cart is None:
            id_cart = self._create_cart()
            self._cart_id_map[session_id] = id_cart
        return self._read_cart(id_cart)

    # -- Mutating: only ever called from a confirmed PendingAction ---------- #

    def add_cart_item(self, cart_id: str, product_id: str, variant_id: str, quantity: int) -> Cart:
        id_product, id_product_attribute = _split_variant_id(variant_id)
        available = self._stock_quantity(id_product, id_product_attribute)
        if available < quantity:
            raise OutOfStockError(f"Variant {variant_id} cannot satisfy quantity {quantity}")

        id_cart = self._get_or_create_ps_cart(cart_id)
        cart_row = self._read_cart_rows(id_cart)
        existing = next(
            (r for r in cart_row if _as_int(r.get("id_product")) == id_product
             and _as_int(r.get("id_product_attribute")) == id_product_attribute),
            None,
        )
        if existing is not None:
            new_quantity = _as_int(existing.get("quantity")) + quantity
        else:
            new_quantity = quantity

        self._upsert_cart_row(id_cart, id_product, id_product_attribute, new_quantity)
        return self._read_cart(id_cart)

    def update_cart_item(self, cart_id: str, variant_id: str, quantity: int) -> Cart:
        id_product, id_product_attribute = _split_variant_id(variant_id)
        if quantity > 0 and self._stock_quantity(id_product, id_product_attribute) < quantity:
            raise OutOfStockError(f"Variant {variant_id} cannot satisfy quantity {quantity}")

        id_cart = self._get_or_create_ps_cart(cart_id)
        self._upsert_cart_row(id_cart, id_product, id_product_attribute, quantity)
        return self._read_cart(id_cart)

    def remove_cart_item(self, cart_id: str, variant_id: str) -> Cart:
        id_product, id_product_attribute = _split_variant_id(variant_id)
        id_cart = self._get_or_create_ps_cart(cart_id)
        self._upsert_cart_row(id_cart, id_product, id_product_attribute, 0)
        return self._read_cart(id_cart)

    def validate_promo(self, cart_id: str, code: str) -> PromoValidation:
        rule = self._find_cart_rule(code)
        if rule is None:
            return PromoValidation(code=code, valid=False, reason="Code not found or inactive")
        if not _as_bool(rule.get("active")):
            return PromoValidation(code=code, valid=False, reason="Code is not active")
        if not self._within_date_range(rule):
            return PromoValidation(code=code, valid=False, reason="Code is expired or not yet active")

        cart = self.get_cart(cart_id)
        minimum_amount = _as_float(rule.get("minimum_amount"))
        if cart.subtotal < minimum_amount:
            return PromoValidation(
                code=code, valid=False,
                reason=f"Cart subtotal {cart.subtotal} is below required {minimum_amount}",
            )

        discount = _as_float(rule.get("reduction_amount")) + round(
            cart.subtotal * _as_float(rule.get("reduction_percent")) / 100, 2
        )
        return PromoValidation(code=code, valid=True, discount_amount=discount)

    def apply_promo(self, cart_id: str, code: str) -> Cart:
        validation = self.validate_promo(cart_id, code)
        if not validation.valid:
            raise PromoInvalidError(validation.reason or f"Invalid promo code: {code}")

        id_cart = self._get_or_create_ps_cart(cart_id)
        rule = self._find_cart_rule(code)
        assert rule is not None  # validate_promo already confirmed it exists
        # There's no webservice association to attach a cart_rule to a cart or order (see
        # __init__'s note), but PrestaShop's own order-total calculation *does* honor an
        # id_cart-scoped `specific_price` (confirmed empirically — it's the same mechanism
        # a catalog-wide sale price uses). Replicate the rule's reduction as one per-line
        # specific_price so the store's real, authoritative total matches what we quoted.
        self._apply_specific_price_discount(id_cart, rule)
        self._applied_promo[id_cart] = code
        return self._read_cart(id_cart)

    def _apply_specific_price_discount(self, id_cart: int, rule: dict) -> None:
        reduction_percent = _as_float(rule.get("reduction_percent"))
        reduction_amount = _as_float(rule.get("reduction_amount"))
        rows = self._read_cart_rows(id_cart)
        for row in rows:
            id_product = _as_int(row.get("id_product"))
            id_product_attribute = _as_int(row.get("id_product_attribute"))
            if reduction_percent:
                reduction, reduction_type = reduction_percent / 100, "percentage"
            elif reduction_amount:
                # A flat cart-wide amount doesn't map cleanly onto a per-line specific_price;
                # applying it once, to the first line, at least gets the real total right for
                # the single-flat-discount case (neither of this project's demo rules uses
                # reduction_amount, so this path isn't exercised by quickstart.md's scenarios).
                reduction, reduction_type = reduction_amount, "amount"
            else:
                continue
            xml = self._build_xml(
                "specific_price",
                {
                    "id_shop": 1,
                    "id_shop_group": 0,
                    "id_cart": id_cart,
                    "id_product": id_product,
                    "id_product_attribute": id_product_attribute,
                    "id_currency": 0,
                    "id_country": 0,
                    "id_group": 0,
                    "id_customer": self._customer_id or 0,
                    "price": -1,
                    "from_quantity": 1,
                    "reduction": reduction,
                    "reduction_tax": 1,
                    "reduction_type": reduction_type,
                    "from": "2020-01-01 00:00:00",
                    "to": "2030-01-01 00:00:00",
                },
            )
            resp = self._xml_request("POST", "/api/specific_prices", xml)
            if resp.status_code >= 400:
                raise _TransportError(
                    f"Failed to apply promo discount to cart {id_cart}: "
                    f"{resp.status_code} {resp.text[:200]}"
                )
            if reduction_amount and not reduction_percent:
                break

    def checkout(self, cart_id: str) -> Order:
        if not (self._customer_id and self._address_id and self._carrier_id):
            raise AdapterUnavailableError(
                "Checkout is not configured: PRESTASHOP_DEFAULT_CUSTOMER_ID/"
                "_ADDRESS_ID/_CARRIER_ID must be set (backend/.env.example) — PrestaShop's "
                "webservice requires a pre-existing customer/address/carrier for order "
                "creation, there is no anonymous checkout at this layer."
            )

        id_cart = self._get_or_create_ps_cart(cart_id)
        cart = self._read_cart(id_cart)
        if not cart.lines:
            raise CartStateChangedError("Cannot checkout an empty cart.")

        for line in cart.lines:
            id_product, id_product_attribute = _split_variant_id(line.variant_id)
            if self._stock_quantity(id_product, id_product_attribute) < line.quantity:
                raise CartStateChangedError(
                    f"Variant {line.variant_id} is no longer available in the requested "
                    f"quantity; a fresh recap is required."
                )

        total = cart.grand_total
        fields = {
            "id_cart": id_cart,
            "id_customer": self._customer_id,
            "id_address_delivery": self._address_id,
            "id_address_invoice": self._address_id,
            "id_carrier": self._carrier_id,
            "id_currency": self._currency_id,
            "id_lang": self._lang_id,
            "id_shop": 1,
            "current_state": self._order_state_id,
            # PrestaShop's order validation rejects any order whose secure_key doesn't
            # match the owning customer's (Validate::isLoadedObject / OrderCore checks) —
            # there is no way to omit or fake this at the webservice layer.
            "secure_key": self._customer_secure_key(),
            "module": self._payment_module,
            "payment": self._payment_label,
            "total_paid": total,
            "total_paid_real": 0,
            "total_products": cart.subtotal,
            "total_products_wt": cart.subtotal,
            "conversion_rate": 1,
            "valid": 0,
        }
        xml_body = self._build_xml("order", fields)
        resp = self._xml_request("POST", "/api/orders", xml_body)
        if resp.status_code >= 400:
            raise CartStateChangedError(
                f"Store rejected order creation ({resp.status_code}): {resp.text[:200]}"
            )
        data = resp.json()
        raw = data.get("order", data)
        order_id = str(raw.get("id", ""))

        # A successful order consumes the cart; drop the local mappings so a later get_cart
        # for this session_id starts a fresh one, matching MockAdapter's post-checkout reset.
        for key, mapped_id in list(self._cart_id_map.items()):
            if mapped_id == id_cart:
                del self._cart_id_map[key]
        self._applied_promo.pop(id_cart, None)

        return Order(id=order_id, cart_id=cart_id, lines=cart.lines, discount_total=cart.discount_total, grand_total=total)

    # -- Internal: cart plumbing ---------------------------------------------- #

    def _get_or_create_ps_cart(self, cart_id: str) -> int:
        id_cart = self._cart_id_map.get(cart_id)
        if id_cart is None:
            id_cart = self._create_cart()
            self._cart_id_map[cart_id] = id_cart
        return id_cart

    def _create_cart(self) -> int:
        fields = {"id_lang": self._lang_id, "id_currency": self._currency_id, "id_shop": 1}
        # Attaching the configured default customer up front (rather than only at checkout)
        # keeps the cart's id_customer consistent with the order placed against it later —
        # PrestaShop's order validation checks the two agree.
        if self._customer_id:
            fields["id_customer"] = self._customer_id
        xml_body = self._build_xml("cart", fields)
        resp = self._xml_request("POST", "/api/carts", xml_body)
        if resp.status_code >= 400:
            raise _TransportError(f"Failed to create cart: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        raw = data.get("cart", data)
        return _as_int(raw.get("id"))

    def _customer_secure_key(self) -> str:
        if self._customer_secure_key_cache is None:
            data = self._get(f"/api/customers/{self._customer_id}")
            raw = (data or {}).get("customer", data or {})
            self._customer_secure_key_cache = raw.get("secure_key", "")
        return self._customer_secure_key_cache

    def _read_cart(self, id_cart: int) -> Cart:
        data = self._get(f"/api/carts/{id_cart}")
        raw = (data or {}).get("cart", data or {})
        rows = self._as_list({"cart_rows": raw.get("associations", {}).get("cart_rows", [])}, "cart_rows", "cart_row")

        lines: list[CartLine] = []
        for row in rows:
            id_product = _as_int(row.get("id_product"))
            id_product_attribute = _as_int(row.get("id_product_attribute"))
            quantity = _as_int(row.get("quantity"))
            if quantity <= 0:
                continue
            unit_price = self._effective_price(id_product, id_product_attribute)
            lines.append(
                CartLine(
                    product_id=str(id_product),
                    variant_id=f"{id_product}#{id_product_attribute}",
                    quantity=quantity,
                    unit_price=unit_price,
                )
            )

        applied_code = None
        discount_total = 0.0
        # The webservice has no association for a cart's applied cart_rule (see
        # __init__'s note) — read back whatever apply_promo() tracked locally instead.
        tracked_code = self._applied_promo.get(id_cart)
        if tracked_code:
            rule = self._find_cart_rule(tracked_code)
            if rule:
                applied_code = tracked_code
                subtotal = round(sum(line.line_total for line in lines), 2)
                discount_total = _as_float(rule.get("reduction_amount")) + round(
                    subtotal * _as_float(rule.get("reduction_percent")) / 100, 2
                )

        return Cart(id=str(id_cart), lines=lines, applied_promo_code=applied_code, discount_total=discount_total)

    def _read_cart_rows(self, id_cart: int) -> list[dict]:
        data = self._get(f"/api/carts/{id_cart}")
        raw = (data or {}).get("cart", data or {})
        return self._as_list({"cart_rows": raw.get("associations", {}).get("cart_rows", [])}, "cart_rows", "cart_row")

    def _upsert_cart_row(self, id_cart: int, id_product: int, id_product_attribute: int, quantity: int) -> None:
        """Replaces the full `cart_rows` association with the caller's desired quantity for
        this product/combination (quantity=0 removes it) — PATCH only requires the id plus
        the changed association, per the webservice's partial-update semantics."""
        rows = [
            r for r in self._read_cart_rows(id_cart)
            if not (_as_int(r.get("id_product")) == id_product and _as_int(r.get("id_product_attribute")) == id_product_attribute)
        ]
        if quantity > 0:
            rows.append({"id_product": id_product, "id_product_attribute": id_product_attribute, "quantity": quantity})

        rows_xml = "".join(
            f"<cart_row><id_product><![CDATA[{r['id_product']}]]></id_product>"
            f"<id_product_attribute><![CDATA[{r['id_product_attribute']}]]></id_product_attribute>"
            f"<quantity><![CDATA[{r['quantity']}]]></quantity></cart_row>"
            for r in rows
        )
        xml_body = self._build_xml("cart", {"id": id_cart}, associations_xml=f"<cart_rows>{rows_xml}</cart_rows>")
        resp = self._xml_request("PATCH", f"/api/carts/{id_cart}", xml_body)
        if resp.status_code >= 400:
            raise _TransportError(f"Failed to update cart {id_cart}: {resp.status_code} {resp.text[:200]}")

    # -- Internal: product/stock/promo lookups -------------------------------- #

    def _map_product(self, raw: dict) -> Product:
        product_id = _as_int(raw.get("id"))
        return Product(
            id=str(product_id),
            name=_localized(raw.get("name"), self._lang_id),
            category_id=str(raw.get("id_category_default", "")),
            base_price=_as_float(raw.get("price")),
            variants=self._load_variants(product_id, _as_float(raw.get("price"))),
        )

    def _load_variants(self, product_id: int, base_price: float) -> list[Variant]:
        combos_data = self._get("/api/combinations", {"display": "full", "filter[id_product]": product_id})
        combos = self._as_list(combos_data, "combinations", "combination")

        if not combos:
            qty = self._stock_quantity(product_id, _NO_COMBINATION_ATTR_ID)
            variant_id = f"{product_id}#{_NO_COMBINATION_ATTR_ID}"
            return [Variant(id=variant_id, attributes={}, price=base_price, in_stock=qty > 0, stock_quantity=qty)]

        values_data = self._get("/api/product_option_values", {"display": "full"})
        values_by_id = {str(v["id"]): v for v in self._as_list(values_data, "product_option_values", "product_option_value")}
        groups_data = self._get("/api/product_options", {"display": "full"})
        group_name_by_id = {
            str(g["id"]): (_localized(g.get("name"), self._lang_id) or g.get("public_name", ""))
            for g in self._as_list(groups_data, "product_options", "product_option")
        }

        variants = []
        for combo in combos:
            id_product_attribute = _as_int(combo.get("id"))
            attribute_ids = [
                str(a.get("id")) for a in self._as_list(
                    {"combos": combo.get("associations", {}).get("product_option_values", [])},
                    "combos", "product_option_value",
                )
            ]
            attributes: dict[str, str] = {}
            for attr_id in attribute_ids:
                value = values_by_id.get(attr_id)
                if value is None:
                    continue
                group_name = group_name_by_id.get(str(value.get("id_attribute_group")), "attribute").lower()
                attributes[group_name] = _localized(value.get("name"), self._lang_id)

            price_impact = _as_float(combo.get("price"))
            qty = self._stock_quantity(product_id, id_product_attribute)
            variants.append(
                Variant(
                    id=f"{product_id}#{id_product_attribute}",
                    attributes=attributes,
                    price=round(base_price + price_impact, 2),
                    in_stock=qty > 0,
                    stock_quantity=qty,
                )
            )
        return variants

    def _stock_quantity(self, id_product: int, id_product_attribute: int) -> int:
        data = self._get(
            "/api/stock_availables",
            {"display": "full", "filter[id_product]": id_product, "filter[id_product_attribute]": id_product_attribute},
        )
        rows = self._as_list(data, "stock_availables", "stock_available")
        if not rows:
            return 0
        return _as_int(rows[0].get("quantity"))

    def _effective_price(self, id_product: int, id_product_attribute: int) -> float:
        product_data = self._get(f"/api/products/{id_product}")
        raw = (product_data or {}).get("product", product_data or {})
        base_price = _as_float(raw.get("price"))
        if id_product_attribute == _NO_COMBINATION_ATTR_ID:
            return base_price
        combo_data = self._get(f"/api/combinations/{id_product_attribute}")
        combo_raw = (combo_data or {}).get("combination", combo_data or {})
        return round(base_price + _as_float(combo_raw.get("price")), 2)

    def _find_cart_rule(self, code: str) -> dict | None:
        data = self._get("/api/cart_rules", {"display": "full", "filter[code]": code})
        rows = self._as_list(data, "cart_rules", "cart_rule")
        for row in rows:
            if str(row.get("code", "")).upper() == code.upper():
                return row
        return None

    @staticmethod
    def _within_date_range(rule: dict) -> bool:
        now = datetime.now(UTC)
        for key, is_start in (("date_from", True), ("date_to", False)):
            raw = rule.get(key)
            if not raw:
                continue
            try:
                bound = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            except ValueError:
                continue
            if is_start and now < bound:
                return False
            if not is_start and now > bound:
                return False
        return True

    @staticmethod
    def _as_list(data: Any, plural_key: str, singular_key: str) -> list[dict]:
        """Normalizes a webservice list response: PrestaShop returns a bare object (not a
        list) when exactly one result matches, and `{}`/None when there are zero."""
        if not data:
            return []
        container = data.get(plural_key, data)
        if container is None:
            return []
        items = container.get(singular_key, container) if isinstance(container, dict) else container
        if items is None:
            return []
        if isinstance(items, list):
            return items
        return [items]
