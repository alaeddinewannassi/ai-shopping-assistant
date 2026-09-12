/** Thin client for the assistant's POST /chat endpoint (T063). */

export interface ProductLink {
  id: string;
  name: string;
}

export interface ClientCartSnapshotRow {
  variant_id: string;
  quantity: number;
}

export interface ClientCartDiscount {
  code: string;
  amount: number;
}

export interface ClientCartAction {
  op: "increment" | "set" | "remove" | "apply_promo";
  // Present for every op except apply_promo.
  variant_id?: string;
  quantity?: number;
  // Present only for apply_promo.
  code?: string;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  needs_confirmation: boolean;
  product_links: ProductLink[];
  show_cart_link: boolean;
  auto_navigate_product_id: string | null;
  auto_navigate_to_cart: boolean;
  // Present only for a session that has been sending cart_snapshot (below), right after a
  // confirmed add/update/remove — the instruction to execute against the store's real
  // front-office cart endpoint (see widget.ts's applyClientCartAction). This is what makes
  // the confirmed mutation actually visible on the store's own cart/checkout pages, instead
  // of only existing in a chat-tracked cart the storefront never sees.
  cart_action: ClientCartAction | null;
  // True only right after a confirmed checkout for a client-cart-synced session — no order
  // was placed server-side; the widget navigates to the store's own real checkout page.
  auto_navigate_to_checkout: boolean;
}

// A hung backend (vs. one that errors quickly) previously left the widget's input disabled
// forever — handleSend()'s try/catch/finally never runs until the fetch itself settles, so
// nothing here means nothing anywhere. Aborting after a bound turns a silent hang into the
// same friendly error path a real network failure already takes.
const DEFAULT_TIMEOUT_MS = 20_000;

export async function sendChatMessage(
  apiBase: string,
  sessionId: string,
  message: string,
  tenantKey?: string,
  customerEmail?: string,
  cartSnapshot?: ClientCartSnapshotRow[],
  cartDiscount?: ClientCartDiscount,
  currentProductId?: string,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  // Sent only when the embed sets a tenant-key attribute (specs/002-backoffice-analytics
  // T207) — omitted entirely resolves to the legacy default tenant, unchanged from before
  // multi-tenancy existed (src/tenancy/resolver.py on the backend).
  if (tenantKey) {
    headers["X-Assistant-Key"] = tenantKey;
  }
  const body: Record<string, unknown> = { session_id: sessionId, message };
  // Only present when the storefront page reports a real, logged-in shopper (see widget.ts's
  // customerEmail getter) — omitted entirely for anonymous/guest browsing, which resolves to
  // the tenant's shared demo identity exactly as before (api/chat.py's ChatRequest).
  if (customerEmail) {
    body.customer_email = customerEmail;
  }
  // Only present when window.prestashop.cart was readable (a real PrestaShop page) — see
  // widget.ts's readClientCartSnapshot. Omitted entirely for a non-browser API caller or a
  // widget embedded outside a PrestaShop page, which keeps using a backend-tracked cart
  // exactly as before this existed (src/session/store.py's client_cart_snapshot docstring).
  if (cartSnapshot !== undefined) {
    body.cart_snapshot = cartSnapshot;
  }
  // Only present when a discount is currently active on the real cart — see widget.ts's
  // readClientCartDiscount. Omitted (never just {amount: 0}) once a code is removed, so the
  // backend's "no discount" default applies exactly the same as if one had never existed.
  if (cartDiscount !== undefined) {
    body.cart_discount = cartDiscount;
  }
  // Only present when window.prestashop.page reports the shopper is on a specific product's
  // page right now (see widget.ts's currentProductId) — the last-resort fallback the backend
  // uses for a product question it otherwise can't pin to one item (real, confirmed live bug:
  // "what materials is this shirt made of?" asked while looking right at that shirt's page).
  if (currentProductId !== undefined) {
    body.current_product_id = currentProductId;
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(`${apiBase}/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!resp.ok) {
      throw new Error(`Assistant service returned ${resp.status}`);
    }
    return (await resp.json()) as ChatResponse;
  } finally {
    clearTimeout(timeout);
  }
}
