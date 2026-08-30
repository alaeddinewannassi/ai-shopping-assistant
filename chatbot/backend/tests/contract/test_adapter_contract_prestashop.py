"""Contract tests for PrestaShopAdapter (contracts/commerce-adapter.md, T012, T011).

Skips entirely unless PRESTASHOP_BASE_URL/PRESTASHOP_API_KEY point at a reachable store —
start it with `docker compose up` (docker/docker-compose.yml) and seed the demo catalog per
quickstart.md before running these for real. Requires quickstart.md's demo fixtures: a
"Classic T-Shirt" product with a Red/M variant, and the WELCOME10/BIGCART15 cart rules.

Outage simulation uses a second adapter instance pointed at a deliberately unused port
(contracts/commerce-adapter.md: "pointing the adapter at an unreachable URL/short timeout")
rather than MockAdapter's `simulate_outage()` toggle, which PrestaShopAdapter has no
equivalent of — see test_adapter_contract.py's module docstring for why the two adapters
don't share one parametrized fixture.
"""

from __future__ import annotations

import os

import httpx
import pytest

from src.adapters.base import AdapterUnavailableError, CartStateChangedError, ProductNotFoundError
from src.adapters.prestashop import PrestaShopAdapter


def _prestashop_reachable() -> bool:
    base_url = os.environ.get("PRESTASHOP_BASE_URL")
    api_key = os.environ.get("PRESTASHOP_API_KEY")
    if not base_url or not api_key:
        return False
    try:
        resp = httpx.get(base_url, auth=(api_key, ""), timeout=2.0)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _prestashop_reachable(),
    reason="PrestaShop reference store not reachable — set PRESTASHOP_BASE_URL/"
    "PRESTASHOP_API_KEY and run `docker compose up` to exercise this adapter.",
)


@pytest.fixture
def adapter() -> PrestaShopAdapter:
    return PrestaShopAdapter()


@pytest.fixture
def unavailable_adapter() -> PrestaShopAdapter:
    """A second adapter instance pointed at an unreachable port, with a short timeout, to
    exercise the AdapterUnavailableError path without touching the real store's state."""
    return PrestaShopAdapter(
        base_url="http://127.0.0.1:1/api",
        api_key=os.environ.get("PRESTASHOP_API_KEY", "unused"),
        timeout_seconds=0.5,
    )


def test_search_products_returns_matches(adapter: PrestaShopAdapter) -> None:
    results = adapter.search_products(query="t-shirt")
    assert any("T-Shirt" in p.name for p in results)


def test_search_products_no_matches_returns_empty_list_not_raise(adapter: PrestaShopAdapter) -> None:
    assert adapter.search_products(query="nonexistent-product-xyz-123") == []


def test_search_products_raises_adapter_unavailable_on_outage(unavailable_adapter: PrestaShopAdapter) -> None:
    with pytest.raises(AdapterUnavailableError):
        unavailable_adapter.search_products(query="anything")


def test_get_product_raises_product_not_found(adapter: PrestaShopAdapter) -> None:
    with pytest.raises(ProductNotFoundError):
        adapter.get_product("999999999")


def test_list_categories_returns_real_categories(adapter: PrestaShopAdapter) -> None:
    assert len(adapter.list_categories()) >= 1


def test_get_cart_creates_empty_cart_on_first_access(adapter: PrestaShopAdapter) -> None:
    cart = adapter.get_cart("contract-test-session-1")
    assert cart.lines == []
    assert cart.subtotal == 0


def test_add_update_remove_cart_item_round_trip(adapter: PrestaShopAdapter) -> None:
    session_id = "contract-test-session-2"
    products = adapter.search_products(query="t-shirt")
    assert products, "demo catalog must include a t-shirt product (quickstart.md)"
    product = products[0]
    variant = product.variants[0]

    cart = adapter.add_cart_item(session_id, product.id, variant.id, 1)
    assert len(cart.lines) == 1

    cart = adapter.update_cart_item(session_id, variant.id, 2)
    assert cart.lines[0].quantity == 2

    cart = adapter.remove_cart_item(session_id, variant.id)
    assert cart.lines == []


def test_validate_promo_invalid_code(adapter: PrestaShopAdapter) -> None:
    result = adapter.validate_promo("contract-test-session-3", "DEFINITELY-NOT-A-REAL-CODE")
    assert result.valid is False
    assert result.reason


def test_checkout_empty_cart_raises_cart_state_changed(adapter: PrestaShopAdapter) -> None:
    with pytest.raises(CartStateChangedError):
        adapter.checkout("contract-test-empty-session")
