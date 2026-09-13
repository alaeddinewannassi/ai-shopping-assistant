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

// Real, confirmed live bug: the access-token cookie expires after 15 minutes
// (backend/src/auth/tokens.py's _ACCESS_TOKEN_TTL) — a real POST /auth/refresh endpoint
// exists to mint a new one from the 30-day refresh token, but nothing here ever called it.
// Every single request just threw its 401 straight up, so any backoffice tab left open (or
// any admin doing more than 15 minutes of work) suddenly saw "Failed to load overview" on
// every page, with no indication why and no way to recover short of manually reloading (a
// reload happens to work today, invisibly, only because a fresh AuthProvider mount re-runs
// api.me() — the same 401, undiscoverably).
//
// Fixed with a standard silent refresh-and-retry: a 401 from anything other than the auth
// endpoints themselves triggers ONE /auth/refresh call, then retries the original request
// once. Concurrent 401s (Overview fires its overview/timeseries queries together) share one
// in-flight refresh via `refreshPromise` rather than each racing their own. If the refresh
// itself fails (the refresh token is ALSO expired/invalid — a genuinely dead session, not
// just an old access token), `onSessionExpired` lets AuthProvider clear its user state so
// the app actually shows the login page instead of a stuck, confusing error screen forever.
let refreshPromise: Promise<void> | null = null;
let onSessionExpired: (() => void) | null = null;

export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler;
}

function refreshAccessToken(): Promise<void> {
  if (!refreshPromise) {
    refreshPromise = fetch(`${API_BASE}/auth/refresh`, { method: "POST", credentials: "include" })
      .then((resp) => {
        if (!resp.ok) throw new ApiError(resp.status, "Session refresh failed");
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

const _NO_RETRY_PATHS = new Set(["/auth/login", "/auth/refresh"]);

async function request<T>(path: string, init?: RequestInit, _isRetry = false): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (resp.status === 204) return undefined as T;
  if (resp.status === 401 && !_isRetry && !_NO_RETRY_PATHS.has(path)) {
    try {
      await refreshAccessToken();
      return await request<T>(path, init, true);
    } catch {
      onSessionExpired?.();
      // Falls through to the original 401 handling below.
    }
  }
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

export interface DailyPoint {
  date: string; // ISO calendar date, YYYY-MM-DD
  session_count: number;
  turn_count: number;
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
  getTimeseries: (tenantId: string, start: string, end: string) =>
    request<DailyPoint[]>(
      `/tenants/${tenantId}/analytics/timeseries?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
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
