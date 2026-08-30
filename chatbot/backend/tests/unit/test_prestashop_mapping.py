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
