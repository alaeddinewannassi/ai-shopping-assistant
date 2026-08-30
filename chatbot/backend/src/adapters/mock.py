"""In-memory CommerceAdapter implementation for fast unit/contract/integration tests.

Satisfies the full CommerceAdapter contract (contracts/commerce-adapter.md) without any
network dependency. Also supports a test-only "simulate outage" mode so tests can exercise
AdapterUnavailableError handling deterministically (T010, T011).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

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
from src.adapters.matching import token_matches_name


@dataclass
class _PromoRule:
    code: str
    discount_amount: float = 0.0
    discount_percent: float = 0.0
    min_subtotal: float = 0.0
    active: bool = True


class MockAdapter:
    """In-memory adapter. One instance = one isolated fake "store"."""

    def __init__(self) -> None:
        self._products: dict[str, Product] = {}
        self._categories: dict[str, Category] = {}
        self._attribute_groups: dict[str, AttributeGroup] = {}
        self._carts: dict[str, Cart] = {}
        self._cart_version: dict[str, int] = {}  # bumped on every mutation, for staleness checks
        self._promo_rules: dict[str, _PromoRule] = {}
        self._orders: dict[str, Order] = {}
        self._simulate_unavailable = False

        self._seed_demo_catalog()

    # -- Test-only controls ------------------------------------------------ #

    def simulate_outage(self, unavailable: bool = True) -> None:
        """Test-only helper: when True, every method raises AdapterUnavailableError,
        mirroring a real store backend that cannot be reached (research.md §8)."""
        self._simulate_unavailable = unavailable

    def _check_available(self) -> None:
        if self._simulate_unavailable:
            raise AdapterUnavailableError("Simulated store outage (MockAdapter test mode).")

    def _seed_demo_catalog(self) -> None:
        """Seeds a small demo catalog matching quickstart.md's fixture description:
        2+ categories, a product with variants, one deliberately out-of-stock product."""
        tshirts = Category(id="cat-tshirts", name="T-Shirts", parent_id=None)
        jackets = Category(id="cat-jackets", name="Jackets", parent_id=None)
        self._categories = {c.id: c for c in [tshirts, jackets]}

        self._attribute_groups = {
            "Color": AttributeGroup(name="Color", values=["Red", "Blue", "Burgundy"]),
            "Size": AttributeGroup(name="Size", values=["S", "M", "L"]),
        }

        red_tshirt = Product(
            id="prod-tshirt-1",
            name="Classic T-Shirt",
            category_id=tshirts.id,
            base_price=19.99,
            variants=[
                Variant(
                    id="var-tshirt-1-red-m",
                    attributes={"color": "Red", "size": "M"},
                    price=19.99,
                    in_stock=True,
                    stock_quantity=25,
                ),
                Variant(
                    id="var-tshirt-1-blue-m",
                    attributes={"color": "Blue", "size": "M"},
                    price=19.99,
                    in_stock=True,
                    stock_quantity=10,
                ),
            ],
        )
        blue_jacket = Product(
            id="prod-jacket-1",
            name="Blue Jacket",
            category_id=jackets.id,
            base_price=89.99,
            variants=[
                Variant(
                    id="var-jacket-1-blue-m",
                    attributes={"color": "Blue", "size": "M"},
                    price=89.99,
                    in_stock=True,
                    stock_quantity=5,
                ),
                Variant(
                    id="var-jacket-1-blue-l",
                    attributes={"color": "Blue", "size": "L"},
                    price=89.99,
                    in_stock=False,
                    stock_quantity=0,
                ),
            ],
        )
        self._products = {p.id: p for p in [red_tshirt, blue_jacket]}

        self._promo_rules = {
            "WELCOME10": _PromoRule(code="WELCOME10", discount_percent=10.0, min_subtotal=0.0),
            "BIGCART15": _PromoRule(code="BIGCART15", discount_percent=15.0, min_subtotal=100.0),
        }

    def _find_variant(self, variant_id: str) -> tuple[Product, Variant]:
        for product in self._products.values():
            for variant in product.variants:
                if variant.id == variant_id:
                    return product, variant
        raise ProductNotFoundError(f"No such variant: {variant_id}")

    # -- Read-only: discovery/navigation ------------------------------------ #

    def search_products(self, query: str = "", filters: dict | None = None) -> list[Product]:
        self._check_available()
        filters = filters or {}
        results = list(self._products.values())

        if query:
            query_tokens = {t for t in query.lower().split() if len(t) > 2}
            # Basic stopword filtering keeps a full sentence ("show me t-shirts under $50")
            # from failing to match a short product name — a real store's search engine
            # would tokenize/rank properly; this is a deliberately simple stand-in.
            stopwords = {
                "show", "me", "the", "a", "an", "please", "for", "with", "under", "over",
                "some", "any", "find", "search", "looking", "want", "need",
            }
            query_tokens -= stopwords
            if query_tokens:
                results = [
                    p for p in results
                    if any(token_matches_name(t, p.name) for t in query_tokens)
                ]
            else:
                # Query was entirely stopwords/too short to extract keywords from — treat
                # as "browse everything" rather than silently returning zero results.
                pass

        category_id = filters.get("category_id")
        if category_id:
            results = [p for p in results if p.category_id == category_id]

        color = filters.get("color")
        if color:
            results = [
                p
                for p in results
                if any(v.attributes.get("color", "").lower() == color.lower() for v in p.variants)
            ]

        max_price = filters.get("max_price")
        if max_price is not None:
            results = [p for p in results if p.base_price <= max_price]

        return results

    def get_product(self, product_id: str) -> Product:
        self._check_available()
        product = self._products.get(product_id)
        if product is None:
            raise ProductNotFoundError(f"No such product: {product_id}")
        return product

    def list_categories(self) -> list[Category]:
        self._check_available()
        return list(self._categories.values())

    def list_attributes(self) -> list[AttributeGroup]:
        self._check_available()
        return list(self._attribute_groups.values())

    def get_cart(self, session_id: str) -> Cart:
        self._check_available()
        if session_id not in self._carts:
            self._carts[session_id] = Cart(id=session_id, lines=[])
            self._cart_version[session_id] = 0
        return self._carts[session_id]

    def set_customer_context(self, cart_id: str, customer_email: str | None) -> None:
        # MockAdapter has no concept of shopper identity — every test/local-dev cart is
        # already isolated by session id alone, so there's nothing to associate.
        pass

    # -- Mutating ------------------------------------------------------------ #

    def add_cart_item(self, cart_id: str, product_id: str, variant_id: str, quantity: int) -> Cart:
        self._check_available()
        _, variant = self._find_variant(variant_id)
        if not variant.in_stock or variant.stock_quantity < quantity:
            raise OutOfStockError(f"Variant {variant_id} cannot satisfy quantity {quantity}")

        cart = self.get_cart(cart_id)
        for line in cart.lines:
            if line.variant_id == variant_id:
                line.quantity += quantity
                break
        else:
            cart.lines.append(
                CartLine(
                    product_id=product_id,
                    variant_id=variant_id,
                    quantity=quantity,
                    unit_price=variant.price,
                )
            )
        self._cart_version[cart_id] = self._cart_version.get(cart_id, 0) + 1
        return cart

    def update_cart_item(self, cart_id: str, variant_id: str, quantity: int) -> Cart:
        self._check_available()
        _, variant = self._find_variant(variant_id)
        if quantity > 0 and (not variant.in_stock or variant.stock_quantity < quantity):
            raise OutOfStockError(f"Variant {variant_id} cannot satisfy quantity {quantity}")

        cart = self.get_cart(cart_id)
        cart.lines = [line for line in cart.lines if line.variant_id != variant_id or quantity > 0]
        for line in cart.lines:
            if line.variant_id == variant_id:
                line.quantity = quantity
        self._cart_version[cart_id] = self._cart_version.get(cart_id, 0) + 1
        return cart

    def remove_cart_item(self, cart_id: str, variant_id: str) -> Cart:
        self._check_available()
        cart = self.get_cart(cart_id)
        cart.lines = [line for line in cart.lines if line.variant_id != variant_id]
        self._cart_version[cart_id] = self._cart_version.get(cart_id, 0) + 1
        return cart

    def validate_promo(self, cart_id: str, code: str) -> PromoValidation:
        self._check_available()
        cart = self.get_cart(cart_id)
        rule = self._promo_rules.get(code.upper())
        if rule is None or not rule.active:
            return PromoValidation(code=code, valid=False, reason="Code not found or inactive")
        if cart.subtotal < rule.min_subtotal:
            return PromoValidation(
                code=code,
                valid=False,
                reason=f"Cart subtotal {cart.subtotal} is below required {rule.min_subtotal}",
            )
        discount = rule.discount_amount + round(cart.subtotal * rule.discount_percent / 100, 2)
        return PromoValidation(code=code, valid=True, discount_amount=discount)

    def apply_promo(self, cart_id: str, code: str) -> Cart:
        self._check_available()
        validation = self.validate_promo(cart_id, code)
        if not validation.valid:
            raise PromoInvalidError(validation.reason or f"Invalid promo code: {code}")
        cart = self.get_cart(cart_id)
        cart.applied_promo_code = code.upper()
        cart.discount_total = validation.discount_amount
        self._cart_version[cart_id] = self._cart_version.get(cart_id, 0) + 1
        return cart

    def checkout(self, cart_id: str) -> Order:
        self._check_available()
        cart = self.get_cart(cart_id)
        if not cart.lines:
            raise CartStateChangedError("Cannot checkout an empty cart.")

        # Re-validate stock for every line at checkout time (spec FR-009 / US3 Scenario 4).
        for line in cart.lines:
            _, variant = self._find_variant(line.variant_id)
            if not variant.in_stock or variant.stock_quantity < line.quantity:
                raise CartStateChangedError(
                    f"Variant {line.variant_id} is no longer available in the requested "
                    f"quantity; a fresh recap is required."
                )

        order = Order(
            id=str(uuid.uuid4()),
            cart_id=cart_id,
            lines=list(cart.lines),
            discount_total=cart.discount_total,
            grand_total=cart.grand_total,
        )
        self._orders[order.id] = order
        # Clear the cart after a successful order, like a real store would.
        cart.lines = []
        cart.applied_promo_code = None
        cart.discount_total = 0.0
        self._cart_version[cart_id] = self._cart_version.get(cart_id, 0) + 1
        return order
