import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type AdminUser } from "./api";

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
 * the user's first membership once it's known. */
const SELECTED_TENANT_KEY = "backoffice-selected-tenant";

export function useSelectedTenant(): [string | null, (tenantId: string) => void] {
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

  return [tenantId, select];
}
