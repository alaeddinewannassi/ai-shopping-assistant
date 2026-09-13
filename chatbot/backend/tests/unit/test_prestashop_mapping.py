"""Unit tests for PrestaShopAdapter's pure webservice-response mapping helpers (T012).

No network/store involved — these exercise the JSON/date parsing quirks documented in
prestashop.py's module docstring in isolation, independent of a live PrestaShop instance.
"""

from __future__ import annotations

import httpx

from src.adapters.base import AdapterUnavailableError
from src.adapters.prestashop import (
    PrestaShopAdapter,
    _as_bool,
    _as_float,
    _as_int,
    _localized,
    _split_variant_id,
    _strip_html,
)

_as_list = PrestaShopAdapter._as_list
_within_date_range = PrestaShopAdapter._within_date_range


def test_localized_unwraps_language_list() -> None:
    value = [{"id": "1", "value": "Classic T-Shirt"}, {"id": "2", "value": "T-Shirt Classique"}]
    assert _localized(value, 1) == "Classic T-Shirt"
    assert _localized(value, 2) == "T-Shirt Classique"


def test_localized_falls_back_to_first_entry_for_missing_language() -> None:
    value = [{"id": "2", "value": "T-Shirt Classique"}]
    assert _localized(value, 1) == "T-Shirt Classique"


def test_localized_tolerates_flat_string() -> None:
    assert _localized("Classic T-Shirt", 1) == "Classic T-Shirt"


def test_localized_unwraps_language_wrapper_dict() -> None:
    value = {"language": [{"id": "1", "value": "Blue Jacket"}]}
    assert _localized(value, 1) == "Blue Jacket"


def test_strip_html_removes_tags_and_collapses_whitespace() -> None:
    assert (
        _strip_html("<p>Regular fit,   <b>short</b>\n sleeves. Made of pima cotton.</p>")
        == "Regular fit, short sleeves. Made of pima cotton."
    )


def test_strip_html_tolerates_plain_text_with_no_tags() -> None:
    assert _strip_html("Made of pima cotton.") == "Made of pima cotton."


def test_as_list_normalizes_single_result_to_list() -> None:
    data = {"products": {"product": {"id": "1"}}}
    assert _as_list(data, "products", "product") == [{"id": "1"}]


def test_as_list_normalizes_multiple_results() -> None:
    data = {"products": {"product": [{"id": "1"}, {"id": "2"}]}}
    assert _as_list(data, "products", "product") == [{"id": "1"}, {"id": "2"}]


def test_as_list_handles_empty_response() -> None:
    assert _as_list(None, "products", "product") == []
    assert _as_list({}, "products", "product") == []


def test_split_variant_id_round_trips() -> None:
    assert _split_variant_id("12#0") == (12, 0)
    assert _split_variant_id("12#7") == (12, 7)


def test_as_float_and_int_and_bool_tolerate_prestashop_string_encoding() -> None:
    assert _as_float("19.99") == 19.99
    assert _as_int("12") == 12
    assert _as_bool("1") is True
    assert _as_bool("0") is False
    assert _as_float(None, 0.0) == 0.0


def test_within_date_range_accepts_no_bounds() -> None:
    assert _within_date_range({}) is True


def test_within_date_range_rejects_expired_code() -> None:
    assert _within_date_range({"date_to": "2000-01-01 00:00:00"}) is False


def test_within_date_range_rejects_not_yet_active_code() -> None:
    assert _within_date_range({"date_from": "2999-01-01 00:00:00"}) is False


def test_missing_env_raises_value_error(monkeypatch) -> None:
    monkeypatch.delenv("PRESTASHOP_BASE_URL", raising=False)
    monkeypatch.delenv("PRESTASHOP_API_KEY", raising=False)
    try:
        PrestaShopAdapter()
    except ValueError as exc:
        assert "PRESTASHOP_BASE_URL" in str(exc)
    else:
        raise AssertionError("expected ValueError when PrestaShop env vars are unset")


# -- specific_price discount resolution (_apply_specific_price) ----------------------- #
#
# Regression coverage for a real bug found via live testing: the storefront showed an
# active, store-wide 20% reduction (€23.90 -> €19.12) that the chatbot's own price quotes
# and cart totals completely ignored, always using the undiscounted catalog `price` field.


def _adapter() -> PrestaShopAdapter:
    return PrestaShopAdapter(base_url="http://prestashop.test/api", api_key="fake-key")


def test_apply_specific_price_applies_an_unscoped_active_percentage_reduction() -> None:
    rows = [{
        "id_product_attribute": "0", "id_shop": "0", "id_shop_group": "0", "id_currency": "0",
        "id_country": "0", "id_group": "0", "id_customer": "0", "from_quantity": "1",
        "price": "-1.000000", "reduction": "0.200000", "reduction_type": "percentage",
        "from": "0000-00-00 00:00:00", "to": "0000-00-00 00:00:00",
    }]
    assert _adapter()._apply_specific_price(23.90, rows) == 19.12


def test_apply_specific_price_applies_a_fixed_override_price() -> None:
    rows = [{
        "id_product_attribute": "0", "id_shop": "0", "id_shop_group": "0", "id_currency": "0",
        "id_country": "0", "id_group": "0", "id_customer": "0", "from_quantity": "1",
        "price": "9.990000", "reduction": "0", "reduction_type": "amount",
        "from": "0000-00-00 00:00:00", "to": "0000-00-00 00:00:00",
    }]
    assert _adapter()._apply_specific_price(23.90, rows) == 9.99


def test_apply_specific_price_skips_a_customer_scoped_rule() -> None:
    rows = [{
        "id_product_attribute": "0", "id_shop": "0", "id_shop_group": "0", "id_currency": "0",
        "id_country": "0", "id_group": "0", "id_customer": "5", "from_quantity": "1",
        "price": "-1.000000", "reduction": "0.500000", "reduction_type": "percentage",
        "from": "0000-00-00 00:00:00", "to": "0000-00-00 00:00:00",
    }]
    assert _adapter()._apply_specific_price(23.90, rows) == 23.90


def test_apply_specific_price_skips_an_expired_rule() -> None:
    rows = [{
        "id_product_attribute": "0", "id_shop": "0", "id_shop_group": "0", "id_currency": "0",
        "id_country": "0", "id_group": "0", "id_customer": "0", "from_quantity": "1",
        "price": "-1.000000", "reduction": "0.500000", "reduction_type": "percentage",
        "from": "0000-00-00 00:00:00", "to": "2000-01-01 00:00:00",
    }]
    assert _adapter()._apply_specific_price(23.90, rows) == 23.90


def test_apply_specific_price_skips_a_bulk_only_rule() -> None:
    rows = [{
        "id_product_attribute": "0", "id_shop": "0", "id_shop_group": "0", "id_currency": "0",
        "id_country": "0", "id_group": "0", "id_customer": "0", "from_quantity": "10",
        "price": "-1.000000", "reduction": "0.500000", "reduction_type": "percentage",
        "from": "0000-00-00 00:00:00", "to": "0000-00-00 00:00:00",
    }]
    assert _adapter()._apply_specific_price(23.90, rows) == 23.90


def test_apply_specific_price_picks_the_lowest_of_several_applicable_rules() -> None:
    common = {
        "id_product_attribute": "0", "id_shop": "0", "id_shop_group": "0", "id_currency": "0",
        "id_country": "0", "id_group": "0", "id_customer": "0", "from_quantity": "1",
        "from": "0000-00-00 00:00:00", "to": "0000-00-00 00:00:00",
    }
    rows = [
        {**common, "price": "-1.000000", "reduction": "0.100000", "reduction_type": "percentage"},
        {**common, "price": "-1.000000", "reduction": "0.300000", "reduction_type": "percentage"},
    ]
    assert _adapter()._apply_specific_price(20.00, rows) == 14.00


def test_apply_specific_price_no_rows_returns_price_unchanged() -> None:
    assert _adapter()._apply_specific_price(23.90, []) == 23.90


# -- get_product's display=full response-shape quirk ----------------------------------- #


def test_get_product_handles_the_plural_wrapped_display_full_response() -> None:
    """Regression test for a real bug introduced (and caught before deploy) alongside the
    specific_price work above: PrestaShop's single-resource GET (/api/products/{id}) returns
    {"product": {...}} normally, but switches to {"products": [{...}]} — PLURAL, wrapped in a
    one-item list — once display=full is added to the request (needed for description/
    description_short, which aren't in the default field set). get_product's parsing must
    handle that shape, not just the singular one, or every field silently comes back empty/
    zero instead of raising or fetching real data."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/products/1":
            return httpx.Response(200, json={"products": [{
                "id": "1",
                "name": "Classic T-Shirt",
                "price": "19.99",
                "id_category_default": "2",
                "description_short": "A soft cotton tee.",
            }]})
        if path == "/api/combinations":
            return httpx.Response(200, json={})
        if path == "/api/specific_prices":
            return httpx.Response(200, json={})
        if path == "/api/stock_availables":
            return httpx.Response(200, json={"stock_availables": {"quantity": "7"}})
        raise AssertionError(f"unexpected request: {path}")

    adapter = _adapter_with_mock_transport(handler)

    product = adapter.get_product("1")

    assert product.name == "Classic T-Shirt"
    assert product.base_price == 19.99
    assert product.description == "A soft cotton tee."
    assert len(product.variants) == 1
    assert product.variants[0].stock_quantity == 7


# -- list_faqs (reads PrestaShop's own content_management_system / CMS pages) -------- #


def test_list_faqs_maps_real_cms_pages_and_strips_html() -> None:
    """Live-verified against a real PrestaShop instance: /api/content_management_system's
    top-level key is the exact resource name (no trailing 's', unlike categories/products),
    and meta_title/content come back as flat strings, not the multi-language list shape
    other fields use — _localized already tolerates both."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/content_management_system"
        return httpx.Response(200, json={"content_management_system": [
            {
                "id": 1,
                "active": "1",
                "meta_title": "Delivery",
                "content": "<h2>Shipments</h2><p>Packages ship within 2 days via UPS.</p>",
            },
            {
                "id": 2,
                "active": "1",
                "meta_title": "",
                "content": "<p>A page with no title should be skipped.</p>",
            },
        ]})

    adapter = _adapter_with_mock_transport(handler)
    faqs = adapter.list_faqs()

    assert len(faqs) == 1
    assert faqs[0].question == "Delivery"
    assert faqs[0].answer == "Shipments Packages ship within 2 days via UPS."


def test_list_faqs_degrades_to_adapter_unavailable_when_permission_not_granted() -> None:
    """Real, confirmed live gap: a webservice key's permissions are granted per-resource in
    PrestaShop's own admin, and content_management_system is not one a key has by default —
    until a merchant grants it, this must degrade the same way every other permission-denied
    resource already does (test_get_converts_a_non_404_4xx_into_adapter_unavailable above),
    not crash the turn."""
    adapter = _adapter_with_mock_transport(
        lambda r: httpx.Response(401, json={"errors": [{"code": 21, "message": "No permission"}]})
    )
    try:
        adapter.list_faqs()
    except AdapterUnavailableError:
        pass
    else:
        raise AssertionError("expected AdapterUnavailableError")


# -- cart_from_snapshot (specs/003-adversarial-qa-review: client-cart-sync) ----------- #
#
# Regression coverage for a real bug found via adversarial review: the chatbot's own
# webservice-created cart and PrestaShop's real front-end session cart were completely
# disconnected — a shopper could confirm an add via chat, be told "Your cart now has...",
# then see an EMPTY cart on the store's own cart page. cart_from_snapshot builds a Cart from
# the shopper's OWN browser-reported contents instead of a cart the storefront never sees.


def test_cart_from_snapshot_builds_lines_with_a_live_looked_up_price() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/products/18":
            return httpx.Response(200, json={"product": {"price": "12.90"}})
        if request.url.path == "/api/combinations/36":
            return httpx.Response(200, json={"combination": {"price": "0"}})
        if request.url.path == "/api/specific_prices":
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request: {request.url.path}")

    adapter = _adapter_with_mock_transport(handler)
    cart = adapter.cart_from_snapshot([{"variant_id": "18#36", "quantity": 2}])

    assert len(cart.lines) == 1
    line = cart.lines[0]
    assert line.product_id == "18"
    assert line.variant_id == "18#36"
    assert line.quantity == 2
    assert line.unit_price == 12.90
    assert cart.subtotal == 25.80


def test_cart_from_snapshot_ignores_a_zero_or_negative_quantity_row() -> None:
    adapter = _adapter_with_mock_transport(
        lambda r: (_ for _ in ()).throw(AssertionError("must not look up a dropped row's price"))
    )
    assert adapter.cart_from_snapshot([{"variant_id": "18#36", "quantity": 0}]).lines == []


def test_cart_from_snapshot_skips_a_malformed_row_without_raising() -> None:
    adapter = _adapter_with_mock_transport(
        lambda r: (_ for _ in ()).throw(AssertionError("must not look up a malformed row's price"))
    )
    assert adapter.cart_from_snapshot([{"quantity": 1}]).lines == []  # missing variant_id


def test_prestashop_adapter_declares_client_cart_sync_support() -> None:
    assert PrestaShopAdapter.supports_client_cart_sync is True


# -- _get's 4xx handling -------------------------------------------------------------- #


def test_get_converts_a_non_404_4xx_into_adapter_unavailable() -> None:
    """Regression test for a real bug found via live testing: a tenant's webservice key
    lacked permission for one resource ("specific_prices"), and PrestaShop's 401 response
    for that was raised as a raw, unhandled _TransportError — outside the circuit breaker's
    retry/conversion path — crashing the whole request with a 500 instead of the graceful
    AdapterUnavailableError every caller in dialogue.py already expects and handles."""
    adapter = _adapter_with_mock_transport(
        lambda r: httpx.Response(401, json={"errors": [{"code": 26, "message": "not allowed"}]})
    )
    try:
        adapter._get("/api/specific_prices", {"filter[id_product]": 1})
    except AdapterUnavailableError:
        pass
    else:
        raise AssertionError("expected AdapterUnavailableError for a non-404 4xx response")


def test_get_still_returns_none_for_a_genuine_404() -> None:
    adapter = _adapter_with_mock_transport(lambda r: httpx.Response(404))
    assert adapter._get("/api/products/999") is None


def test_specific_price_rows_degrades_to_no_reduction_when_permission_denied() -> None:
    """A missing permission for this one, purely-cosmetic pricing resource must not take
    down search/product-details entirely — falls back to "no active reduction known" (the
    undiscounted catalog price) instead of propagating the failure."""
    adapter = _adapter_with_mock_transport(lambda r: httpx.Response(401, json={"errors": []}))
    assert adapter._specific_price_rows(1) == []


# -- The three write-path (_xml_request) callers with the same status-check-outside- #
# -- the-breaker bug as _get, above — real live crash confirmed for the first one -- #


def test_apply_specific_price_discount_converts_a_non_404_4xx_into_adapter_unavailable() -> None:
    """Regression test for a real bug found via live testing: applying a promo code writes
    a per-line specific_price to represent the discount, and this tenant's key also lacked
    permission for that write — the resulting 401 was raised as a raw _TransportError
    (checked after _xml_request/breaker.call already returned, so it escapes unhandled)
    instead of the graceful AdapterUnavailableError _handle_confirm already handles."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"cart": {"associations": {"cart_rows": [
                {"id_product": "1", "id_product_attribute": "0", "quantity": "1"}
            ]}}})
        return httpx.Response(401, json={"errors": [{"code": 26, "message": "not allowed"}]})

    adapter = _adapter_with_mock_transport(handler)
    try:
        adapter._apply_specific_price_discount(1, {"reduction_percent": "10"})
    except AdapterUnavailableError:
        pass
    else:
        raise AssertionError("expected AdapterUnavailableError for a non-404 4xx response")


def test_create_cart_converts_a_non_404_4xx_into_adapter_unavailable() -> None:
    adapter = _adapter_with_mock_transport(
        lambda r: httpx.Response(401, json={"errors": [{"code": 26, "message": "not allowed"}]})
    )
    try:
        adapter._create_cart(customer_id="1")
    except AdapterUnavailableError:
        pass
    else:
        raise AssertionError("expected AdapterUnavailableError for a non-404 4xx response")


def test_upsert_cart_row_converts_a_non_404_4xx_into_adapter_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"cart": {"associations": {"cart_rows": []}}})
        return httpx.Response(401, json={"errors": [{"code": 26, "message": "not allowed"}]})

    adapter = _adapter_with_mock_transport(handler)
    try:
        adapter._upsert_cart_row(1, id_product=1, id_product_attribute=0, quantity=2)
    except AdapterUnavailableError:
        pass
    else:
        raise AssertionError("expected AdapterUnavailableError for a non-404 4xx response")


# -- _get_or_create_ps_cart: cart identity surviving a fresh adapter instance ---------- #


def test_get_or_create_ps_cart_creates_a_new_cart_for_an_unseen_session_id() -> None:
    """The ordinary first-touch path: a session id (never a plain digit string) isn't in the
    fresh adapter's empty _cart_id_map, so a real cart is created and the mapping cached."""
    created = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/carts":
            created["count"] += 1
            return httpx.Response(200, json={"cart": {"id": "42"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    adapter = _adapter_with_mock_transport(handler)
    assert adapter._get_or_create_ps_cart("widget-abc123") == 42
    assert created["count"] == 1
    # Calling again for the SAME session, same (still-warm) adapter instance, must reuse the
    # cached mapping rather than creating a second cart.
    assert adapter._get_or_create_ps_cart("widget-abc123") == 42
    assert created["count"] == 1


def test_get_or_create_ps_cart_uses_an_already_persisted_real_cart_id_directly() -> None:
    """Regression test for a real, confirmed live bug found via adversarial review: a fresh
    PrestaShopAdapter instance (as built on every tenancy/runtime.py TenantRuntime rebuild,
    every 60s) has an empty _cart_id_map — with no fix, the next call for a session whose
    real cart was created by a DIFFERENT (now-discarded) adapter instance would silently
    create a second, empty cart, orphaning the shopper's actual items. Once the dialogue
    layer has persisted the real numeric id onto ConversationSession.cart_id
    (SessionStore.remember_cart_id) and starts passing THAT instead of the session_id, this
    fresh adapter instance must recognize it and use it directly — no new cart, no API call
    at all."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            f"must not make any request to resolve an already-known real cart id: "
            f"{request.method} {request.url.path}"
        )

    adapter = _adapter_with_mock_transport(handler)
    assert adapter._get_or_create_ps_cart("105") == 105


# -- Real-shopper identity resolution (set_customer_context) -------------------------- #


def _adapter_with_mock_transport(handler) -> PrestaShopAdapter:
    adapter = PrestaShopAdapter(
        base_url="http://prestashop.test/api",
        api_key="fake-key",
        default_customer_id="1",
        default_address_id="1",
    )
    adapter._client = httpx.Client(transport=httpx.MockTransport(handler))
    return adapter


def test_resolve_checkout_identity_falls_back_to_demo_defaults_with_no_customer_context() -> None:
    adapter = _adapter_with_mock_transport(lambda r: httpx.Response(200, json={}))
    assert adapter._resolve_checkout_identity("cart-1") == ("1", "1")


def test_resolve_checkout_identity_uses_the_real_customer_and_their_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/customers" in request.url.path:
            return httpx.Response(200, json={"customers": [{"id": "42"}]})
        if "/api/addresses" in request.url.path:
            return httpx.Response(200, json={"addresses": [{"id": "7"}]})
        raise AssertionError(f"unexpected request: {request.url}")

    adapter = _adapter_with_mock_transport(handler)
    adapter.set_customer_context("cart-1", "shopper@example.com")
    assert adapter._resolve_checkout_identity("cart-1") == ("42", "7")


def test_resolve_checkout_identity_falls_back_when_email_is_unknown() -> None:
    adapter = _adapter_with_mock_transport(lambda r: httpx.Response(200, json={"customers": []}))
    adapter.set_customer_context("cart-1", "nobody@example.com")
    assert adapter._resolve_checkout_identity("cart-1") == ("1", "1")


def test_resolve_checkout_identity_falls_back_when_customer_has_no_saved_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/customers" in request.url.path:
            return httpx.Response(200, json={"customers": [{"id": "42"}]})
        return httpx.Response(200, json={"addresses": []})

    adapter = _adapter_with_mock_transport(handler)
    adapter.set_customer_context("cart-1", "shopper@example.com")
    assert adapter._resolve_checkout_identity("cart-1") == ("1", "1")


def test_set_customer_context_none_clears_a_previous_override() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never call out — no override is set")

    adapter = _adapter_with_mock_transport(handler)
    adapter._cart_customer_overrides["cart-1"] = "shopper@example.com"
    adapter.set_customer_context("cart-1", None)
    assert adapter._resolve_checkout_identity("cart-1") == ("1", "1")
