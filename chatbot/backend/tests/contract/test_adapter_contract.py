"""Contract tests for CommerceAdapter (contracts/commerce-adapter.md, T011).

Exercises the full contract against MockAdapter (fast, always runs). PrestaShopAdapter is
covered separately in test_adapter_contract_prestashop.py (T012): its outage-simulation
mechanism (an unreachable URL) differs enough from MockAdapter's `simulate_outage()` toggle
that sharing test bodies via one parametrized fixture would mean either adapter's tests
silently calling a method the other doesn't have, so each gets its own file instead, both
asserting the same contract.
"""

from __future__ import annotations

import pytest

from src.adapters.base import (
    AdapterUnavailableError,
    CartStateChangedError,
    OutOfStockError,
    ProductNotFoundError,
    PromoInvalidError,
)
from src.adapters.mock import MockAdapter


@pytest.fixture
def adapter() -> MockAdapter:
    return MockAdapter()


# -- search_products ---------------------------------------------------------- #


def test_search_products_returns_matches(adapter: MockAdapter) -> None:
    results = adapter.search_products(query="t-shirt")
    assert len(results) == 1
    assert results[0].name == "Classic T-Shirt"


def test_search_products_empty_query_returns_all(adapter: MockAdapter) -> None:
    assert len(adapter.search_products()) == 2


def test_search_products_no_matches_returns_empty_list_not_raise(adapter: MockAdapter) -> None:
    assert adapter.search_products(query="nonexistent-product-xyz") == []


def test_search_products_matches_via_description_when_name_does_not(adapter: MockAdapter) -> None:
    """"cotton" appears only in Classic T-Shirt's description, not its name — search should
    still surface it, not just an exact product-name match."""
    results = adapter.search_products(query="cotton")
    assert len(results) == 1
    assert results[0].name == "Classic T-Shirt"


def test_search_products_filters_by_category(adapter: MockAdapter) -> None:
    results = adapter.search_products(filters={"category_id": "cat-jackets"})
    assert [p.name for p in results] == ["Blue Jacket"]


def test_search_products_raises_adapter_unavailable_on_outage(adapter: MockAdapter) -> None:
    adapter.simulate_outage(True)
    with pytest.raises(AdapterUnavailableError):
        adapter.search_products(query="anything")


# -- get_product ---------------------------------------------------------- #


def test_get_product_returns_product(adapter: MockAdapter) -> None:
    product = adapter.get_product("prod-tshirt-1")
    assert product.name == "Classic T-Shirt"


def test_get_product_raises_product_not_found(adapter: MockAdapter) -> None:
    with pytest.raises(ProductNotFoundError):
        adapter.get_product("does-not-exist")


def test_get_product_raises_adapter_unavailable_on_outage(adapter: MockAdapter) -> None:
    adapter.simulate_outage(True)
    with pytest.raises(AdapterUnavailableError):
        adapter.get_product("prod-tshirt-1")


# -- list_categories / list_attributes (taxonomy grounding, research.md §9) --- #


def test_list_categories_returns_real_categories(adapter: MockAdapter) -> None:
    names = {c.name for c in adapter.list_categories()}
    assert names == {"T-Shirts", "Jackets"}


def test_list_attributes_returns_real_vocabulary(adapter: MockAdapter) -> None:
    groups = {g.name: set(g.values) for g in adapter.list_attributes()}
    assert groups["Color"] == {"Red", "Blue", "Burgundy"}


def test_list_categories_raises_adapter_unavailable_on_outage(adapter: MockAdapter) -> None:
    adapter.simulate_outage(True)
    with pytest.raises(AdapterUnavailableError):
        adapter.list_categories()


# -- get_cart / add_cart_item / update_cart_item / remove_cart_item ----------- #


def test_get_cart_creates_empty_cart_on_first_access(adapter: MockAdapter) -> None:
    cart = adapter.get_cart("new-session")
    assert cart.lines == []
    assert cart.subtotal == 0


def test_add_cart_item_adds_line_and_reflects_price(adapter: MockAdapter) -> None:
    cart = adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 2)
    assert len(cart.lines) == 1
    assert cart.subtotal == pytest.approx(39.98)


def test_add_cart_item_raises_out_of_stock(adapter: MockAdapter) -> None:
    with pytest.raises(OutOfStockError):
        adapter.add_cart_item("s1", "prod-jacket-1", "var-jacket-1-blue-l", 1)


def test_add_cart_item_raises_adapter_unavailable_on_outage(adapter: MockAdapter) -> None:
    adapter.simulate_outage(True)
    with pytest.raises(AdapterUnavailableError):
        adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)


def test_update_cart_item_changes_quantity(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)
    cart = adapter.update_cart_item("s1", "var-tshirt-1-red-m", 5)
    assert cart.lines[0].quantity == 5


def test_update_cart_item_zero_quantity_removes_line(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)
    cart = adapter.update_cart_item("s1", "var-tshirt-1-red-m", 0)
    assert cart.lines == []


def test_remove_cart_item_removes_line(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)
    cart = adapter.remove_cart_item("s1", "var-tshirt-1-red-m")
    assert cart.lines == []


def test_remove_cart_item_raises_adapter_unavailable_on_outage(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)
    adapter.simulate_outage(True)
    with pytest.raises(AdapterUnavailableError):
        adapter.remove_cart_item("s1", "var-tshirt-1-red-m")


# -- validate_promo / apply_promo --------------------------------------------- #


def test_validate_promo_valid_code(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)
    result = adapter.validate_promo("s1", "WELCOME10")
    assert result.valid is True
    assert result.discount_amount > 0


def test_validate_promo_invalid_code(adapter: MockAdapter) -> None:
    result = adapter.validate_promo("s1", "FAKE123")
    assert result.valid is False
    assert result.reason


def test_validate_promo_below_threshold(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)  # $19.99, below $100
    result = adapter.validate_promo("s1", "BIGCART15")
    assert result.valid is False


def test_apply_promo_updates_cart_discount(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)
    cart = adapter.apply_promo("s1", "WELCOME10")
    assert cart.applied_promo_code == "WELCOME10"
    assert cart.discount_total > 0


def test_apply_promo_raises_promo_invalid(adapter: MockAdapter) -> None:
    with pytest.raises(PromoInvalidError):
        adapter.apply_promo("s1", "FAKE123")


def test_apply_promo_raises_adapter_unavailable_on_outage(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)
    adapter.simulate_outage(True)
    with pytest.raises(AdapterUnavailableError):
        adapter.apply_promo("s1", "WELCOME10")


# -- checkout ------------------------------------------------------------ #


def test_checkout_creates_order_from_cart(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 2)
    order = adapter.checkout("s1")
    assert order.grand_total == pytest.approx(39.98)
    assert order.id


def test_checkout_empty_cart_raises_cart_state_changed(adapter: MockAdapter) -> None:
    with pytest.raises(CartStateChangedError):
        adapter.checkout("empty-session")


def test_checkout_out_of_stock_line_raises_cart_state_changed(adapter: MockAdapter) -> None:
    # Add a valid item, then have the store's stock change out from under it.
    adapter.add_cart_item("s1", "prod-jacket-1", "var-jacket-1-blue-m", 1)
    _, variant = adapter._find_variant("var-jacket-1-blue-m")  # test-only introspection
    variant.in_stock = False
    variant.stock_quantity = 0
    with pytest.raises(CartStateChangedError):
        adapter.checkout("s1")


def test_checkout_raises_adapter_unavailable_on_outage(adapter: MockAdapter) -> None:
    adapter.add_cart_item("s1", "prod-tshirt-1", "var-tshirt-1-red-m", 1)
    adapter.simulate_outage(True)
    with pytest.raises(AdapterUnavailableError):
        adapter.checkout("s1")
