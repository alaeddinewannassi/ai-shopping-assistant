import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setSessionExpiredHandler, type AdminUser } from "./api";

interface AuthState {
  user: AdminUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AdminUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  // A definitively dead session (the refresh token is also expired/invalid, not just the
  // 15-minute access token) — api.ts's request() calls this after its own silent
  // refresh-and-retry fails, so ProtectedShell's `!user` check redirects to /login instead
  // of leaving every page stuck on "Failed to load X" forever.
  useEffect(() => {
    setSessionExpiredHandler(() => setUser(null));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const loggedInUser = await api.login(email, password);
    setUser(loggedInUser);
  }, []);

  const logout = useCallback(async () => {
    await api.logout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

/** The currently selected tenant — persisted so a page refresh doesn't lose it. Defaults to
 * the user's first membership once it's known.
 *
 * Real, confirmed live bug: this used to be a plain hook with its own `useState`, so every
 * call site (AppShell's switcher, Overview, Funnel, Sessions, SessionDetail, Settings) held
 * an independent copy, only synchronized via localStorage at each component's own mount
 * time. Switching tenants in AppShell's dropdown updated localStorage and AppShell's own
 * copy, but every page component's already-mounted copy never re-rendered — so every
 * analytics page kept showing whichever tenant was selected at the last full page load,
 * silently ignoring the switcher afterward. Every affected page's own useQuery already
 * correctly keys on tenantId (would have refetched fine) — the state just never actually
 * changed for them. Fixed by making this genuinely shared context state, the same pattern
 * already used for auth above, instead of a hook with private per-call-site state. */
const SELECTED_TENANT_KEY = "backoffice-selected-tenant";

interface SelectedTenantState {
  tenantId: string | null;
  select: (tenantId: string) => void;
}

const SelectedTenantContext = createContext<SelectedTenantState | null>(null);

export function SelectedTenantProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [tenantId, setTenantId] = useState<string | null>(() =>
    localStorage.getItem(SELECTED_TENANT_KEY),
  );

  useEffect(() => {
    if (tenantId || !user) return;
    const first = user.memberships[0]?.tenant_id;
    if (first) setTenantId(first);
  }, [user, tenantId]);

  const select = useCallback((id: string) => {
    localStorage.setItem(SELECTED_TENANT_KEY, id);
    setTenantId(id);
  }, []);

  return (
    <SelectedTenantContext.Provider value={{ tenantId, select }}>
      {children}
    </SelectedTenantContext.Provider>
  );
}

export function useSelectedTenant(): [string | null, (tenantId: string) => void] {
  const ctx = useContext(SelectedTenantContext);
  if (!ctx) throw new Error("useSelectedTenant must be used within SelectedTenantProvider");
  return [ctx.tenantId, ctx.select];
}
