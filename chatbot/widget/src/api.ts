/** Thin client for the assistant's POST /chat endpoint (T063). */

export interface ChatResponse {
  session_id: string;
  reply: string;
  needs_confirmation: boolean;
}

export async function sendChatMessage(
  apiBase: string,
  sessionId: string,
  message: string,
  tenantKey?: string,
): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  // Sent only when the embed sets a tenant-key attribute (specs/002-backoffice-analytics
  // T207) — omitted entirely resolves to the legacy default tenant, unchanged from before
  // multi-tenancy existed (src/tenancy/resolver.py on the backend).
  if (tenantKey) {
    headers["X-Assistant-Key"] = tenantKey;
  }
  const resp = await fetch(`${apiBase}/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  if (!resp.ok) {
    throw new Error(`Assistant service returned ${resp.status}`);
  }
  return (await resp.json()) as ChatResponse;
}
