"""Commerce Adapter interface (Constitution Principle II).

This is the single integration seam between the assistant's dialogue/agent layer and any
e-commerce backend. Concrete implementations: `PrestaShopAdapter` (real store, via the
PrestaShop Webservice REST API) and `MockAdapter` (in-memory, for fast tests).

See specs/001-ai-shopping-assistant/contracts/commerce-adapter.md for the full behavioral
contract every method below must satisfy, including AdapterUnavailableError semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


# --------------------------------------------------------------------------- #
# Domain models (see specs/001-ai-shopping-assistant/data-model.md)
# --------------------------------------------------------------------------- #


@dataclass
class Variant:
    id: str
    attributes: dict[str, str]  # e.g. {"color": "Red", "size": "M"}
    price: float
    in_stock: bool
    stock_quantity: int


@dataclass
class Product:
    id: str
    name: str
    category_id: str
    base_price: float
    variants: list[Variant] = field(default_factory=list)


@dataclass
class Category:
    id: str
    name: str
    parent_id: Optional[str] = None


@dataclass
class AttributeGroup:
    name: str
    values: list[str] = field(default_factory=list)


@dataclass
class CartLine:
    product_id: str
    variant_id: str
    quantity: int
    unit_price: float

    @property
    def line_total(self) -> float:
        return round(self.unit_price * self.quantity, 2)


@dataclass
class Cart:
    id: str
    lines: list[CartLine] = field(default_factory=list)
    applied_promo_code: Optional[str] = None
    discount_total: float = 0.0

    @property
    def subtotal(self) -> float:
        return round(sum(line.line_total for line in self.lines), 2)

    @property
    def grand_total(self) -> float:
        return round(self.subtotal - self.discount_total, 2)


@dataclass
class PromoValidation:
    code: str
    valid: bool
    discount_amount: float = 0.0
    reason: Optional[str] = None  # populated when valid is False


@dataclass
class Order:
    id: str
    cart_id: str
    lines: list[CartLine]
    discount_total: float
    grand_total: float


# --------------------------------------------------------------------------- #
# Shared error vocabulary (contracts/commerce-adapter.md "Error Types" table)
# --------------------------------------------------------------------------- #


class ProductNotFoundError(Exception):
    """Raised by get_product when the referenced product id doesn't exist."""


class OutOfStockError(Exception):
    """Raised by add/update_cart_item when the requested quantity/variant is unavailable."""


class PromoInvalidError(Exception):
    """Raised by apply_promo when the code is not valid at apply time."""


class CartStateChangedError(Exception):
    """Raised by checkout when cart state changed since it was last read; re-recap required."""


class AdapterUnavailableError(Exception):
    """Raised by any CommerceAdapter method on a genuine transport/timeout failure.

    Distinct from business errors above: this means "couldn't ask the store", not "the
    store validly said no". See research.md §8 for read-vs-mutate fallback handling and
    contracts/commerce-adapter.md for the full behavioral contract.
    """


# --------------------------------------------------------------------------- #
# The interface itself
# --------------------------------------------------------------------------- #


@runtime_checkable
class CommerceAdapter(Protocol):
    """Platform-agnostic commerce integration interface.

    Every method that can mutate store state (add/update/remove cart item, apply_promo,
    checkout) MUST only ever be called by the agent layer's confirmed PendingAction handler
    (backend/src/agent/pending.py) — never directly from intent parsing / LLM tool output
    (research.md §9.3). This restriction is enforced by the agent layer's tool-calling
    schema, not by this interface itself.
    """

    # -- Read-only: discovery/navigation ---------------------------------- #

    def search_products(self, query: str = "", filters: Optional[dict] = None) -> list[Product]:
        """Read-only. Returns [] on no matches. Raises AdapterUnavailableError on outage."""
        ...

    def get_product(self, product_id: str) -> Product:
        """Read-only. Raises ProductNotFoundError / AdapterUnavailableError."""
        ...

    def list_categories(self) -> list[Category]:
        """Read-only. Backs the TaxonomyResolver (research.md §9, contracts/taxonomy-resolver.md)."""
        ...

    def list_attributes(self) -> list[AttributeGroup]:
        """Read-only. Backs the TaxonomyResolver (research.md §9, contracts/taxonomy-resolver.md)."""
        ...

    def get_cart(self, session_id: str) -> Cart:
        """Read-only. Creates an empty Cart on first access if none exists yet."""
        ...

    def set_customer_context(self, cart_id: str, customer_email: str | None) -> None:
        """Associates this cart_id (a chat session) with a real, logged-in shopper's email —
        or clears it when None. Optional per-platform behavior: an adapter with no concept
        of shopper identity (e.g. MockAdapter) may no-op. See PrestaShopAdapter's own
        docstring for the trust model this is built on."""
        ...

    # -- Mutating: only ever called from a confirmed PendingAction --------- #

    def add_cart_item(
        self, cart_id: str, product_id: str, variant_id: str, quantity: int
    ) -> Cart:
        """Raises OutOfStockError / AdapterUnavailableError."""
        ...

    def update_cart_item(self, cart_id: str, variant_id: str, quantity: int) -> Cart:
        """Raises OutOfStockError / AdapterUnavailableError."""
        ...

    def remove_cart_item(self, cart_id: str, variant_id: str) -> Cart:
        """Raises AdapterUnavailableError."""
        ...

    def validate_promo(self, cart_id: str, code: str) -> PromoValidation:
        """Read-only validation call (does not mutate the cart). Raises AdapterUnavailableError."""
        ...

    def apply_promo(self, cart_id: str, code: str) -> Cart:
        """Raises PromoInvalidError / AdapterUnavailableError."""
        ...

    def checkout(self, cart_id: str) -> Order:
        """Raises CartStateChangedError / AdapterUnavailableError."""
        ...
