"""Regression test for a real, confirmed live bug found via adversarial review
(specs/003-adversarial-qa-review): a shopper's cart silently "emptied" after roughly a
minute of inactivity, because tenancy/runtime.py's TenantRuntime cache (60s TTL) rebuilds a
brand-new PrestaShopAdapter — with a brand-new, empty `_cart_id_map` — and nothing had ever
persisted the real PrestaShop cart id anywhere durable. The next mutating call then silently
created a second, empty cart, orphaning the shopper's real one.

This test simulates that exact rebuild: two independent PrestaShopAdapter instances (never
the same object, exactly like two different TenantRuntime generations) share one fake
PrestaShop backend and one SessionStore, and a cart started on the first instance must still
be found by the second.
"""

from __future__ import annotations

import re

import httpx

from src.adapters.prestashop import PrestaShopAdapter
from src.agent.pending import PendingActionGate
from src.session.store import SessionStore

_CART_ROW = re.compile(
    r"<id_product><!\[CDATA\[(\d+)\]\]></id_product>"
    r"<id_product_attribute><!\[CDATA\[(\d+)\]\]></id_product_attribute>"
    r"<quantity><!\[CDATA\[(\d+)\]\]></quantity>"
)


class _FakePrestaShopBackend:
    """A minimal, stateful fake of just enough of the PrestaShop webservice for an
    add_cart_item -> confirm round trip: cart creation/read/update, one product's price, and
    unlimited stock. Shared across multiple PrestaShopAdapter instances via one handler
    closure, exactly like the real PrestaShop container is shared across TenantRuntime
    rebuilds in production."""

    def __init__(self) -> None:
        self.carts: dict[int, list[tuple[int, int, int]]] = {}
        self._next_id = 100
        self.cart_creations = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method

        if method == "POST" and path == "/api/carts":
            self._next_id += 1
            self.carts[self._next_id] = []
            self.cart_creations += 1
            return httpx.Response(200, json={"cart": {"id": str(self._next_id)}})

        if method == "PATCH" and path.startswith("/api/carts/"):
            id_cart = int(path.rsplit("/", 1)[-1])
            body = request.content.decode("utf-8")
            rows = [(int(p), int(a), int(q)) for p, a, q in _CART_ROW.findall(body)]
            self.carts[id_cart] = rows
            return httpx.Response(200, json={"cart": {"id": str(id_cart)}})

        if method == "GET" and path.startswith("/api/carts/"):
            id_cart = int(path.rsplit("/", 1)[-1])
            rows = self.carts.get(id_cart, [])
            # Mirrors the real webservice's JSON-via-output_format=JSON shape closely enough
            # for PrestaShopAdapter._as_list to parse one or many rows uniformly.
            return httpx.Response(
                200,
                json={
                    "cart": {
                        "id": str(id_cart),
                        "associations": {
                            "cart_rows": [
                                {"id_product": str(p), "id_product_attribute": str(a), "quantity": str(q)}
                                for p, a, q in rows
                            ]
                        },
                    }
                },
            )

        if method == "GET" and path == "/api/stock_availables":
            return httpx.Response(200, json={"stock_availables": {"quantity": "100"}})

        if method == "GET" and path.startswith("/api/products/"):
            return httpx.Response(200, json={"product": {"price": "10.00"}})

        if method == "GET" and path == "/api/specific_prices":
            return httpx.Response(200, json={})

        raise AssertionError(f"unexpected request in fake backend: {method} {path}")


def _adapter(backend: _FakePrestaShopBackend) -> PrestaShopAdapter:
    adapter = PrestaShopAdapter(
        base_url="http://prestashop.test/api",
        api_key="fake-key",
        default_customer_id="1",
        default_address_id="1",
    )
    adapter._client = httpx.Client(transport=httpx.MockTransport(backend.handle))
    return adapter


def test_cart_survives_a_tenant_runtime_rebuild_across_two_adapter_instances() -> None:
    backend = _FakePrestaShopBackend()
    session_store = SessionStore(redis_url=None)

    # Generation 1 (before the simulated TenantRuntime rebuild): add an item and confirm it.
    gate_gen1 = PendingActionGate(session_store, _adapter(backend))
    action = gate_gen1.propose(
        "shopper-1", "add_cart_item",
        {"product_id": "7", "variant_id": "7#0", "quantity": 1},
        "Add 1 x Widget?",
    )
    result = gate_gen1.confirm("shopper-1", action.action_id)
    assert result.cart is not None
    assert [line.product_id for line in result.cart.lines] == ["7"]
    assert backend.cart_creations == 1

    session = session_store.get_or_create("shopper-1")
    assert session.cart_id is not None, (
        "SessionStore.remember_cart_id must have persisted the real PrestaShop cart id onto "
        "the session — without it, a fresh adapter instance has no way to find this cart."
    )

    # Generation 2: a BRAND NEW PrestaShopAdapter instance (its own empty _cart_id_map),
    # exactly like tenancy/runtime.py builds after the 60s TenantRuntime TTL — sharing only
    # the same fake backend and the same SessionStore, never the same Python object.
    gate_gen2 = PendingActionGate(session_store, _adapter(backend))
    action2 = gate_gen2.propose(
        "shopper-1", "add_cart_item",
        {"product_id": "7", "variant_id": "7#0", "quantity": 1},
        "Add 1 more x Widget?",
    )
    result2 = gate_gen2.confirm("shopper-1", action2.action_id)

    assert result2.cart is not None
    # The SAME cart, now with quantity 2 — not a second, empty cart from a lost mapping.
    assert result2.cart.lines[0].quantity == 2
    assert backend.cart_creations == 1, (
        "a second real cart was created — the shopper's original cart became silently "
        "unreachable after the simulated adapter rebuild, reproducing the live bug"
    )
