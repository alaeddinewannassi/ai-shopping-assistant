/** Thin client for backoffice/backend's HTTP API (specs/002-backoffice-analytics/contracts/
 * admin-api.yaml). Every call sends cookies (`credentials: "include"`) — auth is httpOnly
 * cookies, never a bearer header (src/auth/tokens.py on the backend). */

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8001";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (resp.status === 204) return undefined as T;
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new ApiError(resp.status, body.detail ?? resp.statusText);
  }
  return (await resp.json()) as T;
}

export interface Membership {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  role: "owner" | "admin" | "analyst" | "support";
}

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  is_superadmin: boolean;
  memberships: Membership[];
}

export interface Tenant {
  id: string;
  slug: string;
  name: string;
  status: "active" | "suspended";
  plan: string;
}

export interface OverviewMetrics {
  session_count: number;
  turn_count: number;
  ordered_session_count: number;
  conversion_rate: number;
  avg_turn_latency_ms: number | null;
  p95_turn_latency_ms: number | null;
  error_event_count: number;
  error_rate: number;
}

export interface FunnelMetrics {
  sessions: number;
  discovery: number;
  proposal: number;
  confirmed: number;
  cart_mutated: number;
  checkout_proposed: number;
  ordered: number;
}

export interface SessionSummary {
  session_id: string;
  started_at: string;
  last_seen_at: string;
  turn_count: number;
  outcome: "browsing" | "cart" | "ordered" | "abandoned";
  cart_id: string | null;
  order_id: string | null;
}

export interface AssistantEventOut {
  turn_id: string;
  seq: number;
  occurred_at: string;
  intent: string;
  action: string;
  outcome: string;
  details: Record<string, unknown>;
  turn_elapsed_ms: number | null;
}

export interface AdapterConfig {
  platform: string;
  base_url: string;
  api_key: string | null;
  host_header: string | null;
  lang_id: number;
  default_customer_id: string | null;
  default_address_id: string | null;
  default_carrier_id: string | null;
  default_currency_id: string | null;
  default_order_state_id: string | null;
  payment_module: string | null;
  payment_label: string | null;
  is_active: boolean;
}

export interface LlmConfig {
  provider: string;
  model: string | null;
  api_key: string | null;
  monthly_token_budget: number | null;
  budget_action: string;
  is_active: boolean;
}

export interface WidgetKey {
  id: string;
  public_key: string;
  allowed_origins: string[];
  is_active: boolean;
  last_used_at: string | null;
}

export interface PromoRule {
  rule_id: string;
  condition: string;
  target_code: string;
  priority: number;
  stackable_with: string[];
  is_active: boolean;
}

export const api = {
  login: (email: string, password: string) =>
    request<AdminUser>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<AdminUser>("/auth/me"),

  listTenants: () => request<Tenant[]>("/tenants"),
  createTenant: (slug: string, name: string) =>
    request<Tenant>("/tenants", { method: "POST", body: JSON.stringify({ slug, name }) }),
  getTenant: (tenantId: string) => request<Tenant>(`/tenants/${tenantId}`),
  updateTenant: (tenantId: string, patch: Partial<Pick<Tenant, "name" | "status" | "plan">>) =>
    request<Tenant>(`/tenants/${tenantId}`, { method: "PATCH", body: JSON.stringify(patch) }),

  getOverview: (tenantId: string, start: string, end: string) =>
    request<OverviewMetrics>(
      `/tenants/${tenantId}/analytics/overview?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    ),
  getFunnel: (tenantId: string, start: string, end: string) =>
    request<FunnelMetrics>(
      `/tenants/${tenantId}/analytics/funnel?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    ),

  listSessions: (tenantId: string, opts?: { limit?: number; outcome?: string }) => {
    const params = new URLSearchParams();
    if (opts?.limit) params.set("limit", String(opts.limit));
    if (opts?.outcome) params.set("outcome", opts.outcome);
    const qs = params.toString();
    return request<SessionSummary[]>(`/tenants/${tenantId}/sessions${qs ? `?${qs}` : ""}`);
  },
  getSessionEvents: (tenantId: string, sessionId: string) =>
    request<AssistantEventOut[]>(`/tenants/${tenantId}/sessions/${sessionId}/events`),

  getAdapterConfig: (tenantId: string) =>
    request<AdapterConfig>(`/tenants/${tenantId}/adapter-config`),
  upsertAdapterConfig: (tenantId: string, config: Record<string, unknown>) =>
    request<AdapterConfig>(`/tenants/${tenantId}/adapter-config`, {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  getLlmConfig: (tenantId: string) => request<LlmConfig>(`/tenants/${tenantId}/llm-config`),
  upsertLlmConfig: (tenantId: string, config: Record<string, unknown>) =>
    request<LlmConfig>(`/tenants/${tenantId}/llm-config`, {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  listWidgetKeys: (tenantId: string) => request<WidgetKey[]>(`/tenants/${tenantId}/widget-keys`),
  issueWidgetKey: (tenantId: string, allowedOrigins: string[]) =>
    request<WidgetKey>(`/tenants/${tenantId}/widget-keys`, {
      method: "POST",
      body: JSON.stringify({ allowed_origins: allowedOrigins }),
    }),
  revokeWidgetKey: (tenantId: string, keyId: string) =>
    request<void>(`/tenants/${tenantId}/widget-keys/${keyId}`, { method: "DELETE" }),

  listPromoRules: (tenantId: string) => request<PromoRule[]>(`/tenants/${tenantId}/promo-rules`),
  upsertPromoRule: (tenantId: string, ruleId: string, rule: Record<string, unknown>) =>
    request<PromoRule>(`/tenants/${tenantId}/promo-rules/${ruleId}`, {
      method: "PUT",
      body: JSON.stringify(rule),
    }),
};
