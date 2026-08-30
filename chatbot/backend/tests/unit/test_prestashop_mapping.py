"""Unit tests for PrestaShopAdapter's pure webservice-response mapping helpers (T012).

No network/store involved — these exercise the JSON/date parsing quirks documented in
prestashop.py's module docstring in isolation, independent of a live PrestaShop instance.
"""

from __future__ import annotations

import httpx

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
