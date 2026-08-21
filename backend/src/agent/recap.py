"""Human-readable recap text builders for cart-mutation PendingActions (T034).

Every recap shown to a shopper before a cart mutation must include product, variant
attributes, quantity, and unit price (spec US2 Scenario 1) so the confirmation is
meaningful, not just a bare "yes/no".
"""

from __future__ import annotations

from src.adapters.base import Cart, CartLine, Product, Variant


def _variant_label(variant: Variant) -> str:
    attrs = ", ".join(f"{k}: {v}" for k, v in variant.attributes.items())
    return f"({attrs})" if attrs else ""


def build_add_to_cart_recap(product: Product, variant: Variant, quantity: int) -> str:
    label = _variant_label(variant)
    total = round(variant.price * quantity, 2)
    return (
        f"Add {quantity} x {product.name} {label} at ${variant.price:.2f} each "
        f"(${total:.2f} total) to your cart?"
    ).replace("  ", " ")


def build_update_cart_recap(product: Product, line: CartLine, new_quantity: int) -> str:
    total = round(line.unit_price * new_quantity, 2)
    return (
        f"Change {product.name} (currently {line.quantity}) to quantity {new_quantity} "
        f"(${total:.2f} total)?"
    )


def build_remove_cart_recap(product: Product, line: CartLine) -> str:
    return f"Remove {product.name} (qty {line.quantity}) from your cart?"


def build_cart_summary(cart: Cart, products_by_id: dict[str, Product]) -> str:
    """Renders the post-mutation cart state shown after a confirmed change (US2 Scenario 2)."""
    if not cart.lines:
        return "Your cart is now empty."
    parts = []
    for line in cart.lines:
        product = products_by_id.get(line.product_id)
        name = product.name if product else line.product_id
        parts.append(f"{line.quantity} x {name} (${line.line_total:.2f})")
    summary = "; ".join(parts)
    return f"Your cart now has: {summary}. Subtotal: ${cart.subtotal:.2f}."


def build_checkout_recap(cart: Cart, products_by_id: dict[str, Product]) -> str:
    """Full pre-order recap for the `checkout` PendingAction (T044): every line item,
    quantity, unit price, any applied discount, and the grand total (spec FR-007)."""
    parts = []
    for line in cart.lines:
        product = products_by_id.get(line.product_id)
        name = product.name if product else line.product_id
        parts.append(
            f"{line.quantity} x {name} @ ${line.unit_price:.2f} each (${line.line_total:.2f})"
        )
    lines_text = "; ".join(parts)
    discount_text = ""
    if cart.discount_total:
        code_text = f" (code {cart.applied_promo_code})" if cart.applied_promo_code else ""
        discount_text = f" Discount{code_text}: -${cart.discount_total:.2f}."
    return (
        f"Here's your order recap: {lines_text}. Subtotal: ${cart.subtotal:.2f}."
        f"{discount_text} Grand total: ${cart.grand_total:.2f}."
    )
