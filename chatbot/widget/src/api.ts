/** Thin client for the assistant's POST /chat endpoint (T063). */

export interface ProductLink {
  id: string;
  name: string;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  needs_confirmation: boolean;
  product_links: ProductLink[];
  show_cart_link: boolean;
  auto_navigate_product_id: string | null;
  auto_navigate_to_cart: boolean;
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
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  // Sent only when the embed sets a tenant-key attribute (specs/002-backoffice-analytics
  // T207) — omitted entirely resolves to the legacy default tenant, unchanged from before
  // multi-tenancy existed (src/tenancy/resolver.py on the backend).
  if (tenantKey) {
    headers["X-Assistant-Key"] = tenantKey;
  }
  const body: Record<string, string> = { session_id: sessionId, message };
  // Only present when the storefront page reports a real, logged-in shopper (see widget.ts's
  // customerEmail getter) — omitted entirely for anonymous/guest browsing, which resolves to
  // the tenant's shared demo identity exactly as before (api/chat.py's ChatRequest).
  if (customerEmail) {
    body.customer_email = customerEmail;
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
