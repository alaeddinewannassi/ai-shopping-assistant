import { NavLink, Outlet } from "react-router-dom";
import { useAuth, useSelectedTenant } from "../lib/auth";

const NAV_ITEMS = [
  { to: "/overview", label: "Overview" },
  { to: "/funnel", label: "Funnel" },
  { to: "/sessions", label: "Sessions" },
  { to: "/settings", label: "Settings" },
];

export function AppShell() {
  const { user, logout } = useAuth();
  const [tenantId, selectTenant] = useSelectedTenant();

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <nav
        style={{
          width: 220,
          borderRight: "1px solid var(--border)",
          padding: 16,
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div style={{ fontWeight: 700 }}>Backoffice</div>

        {user && user.memberships.length > 1 && (
          <select
            value={tenantId ?? ""}
            onChange={(e) => selectTenant(e.target.value)}
            style={{ padding: 6, borderRadius: 6 }}
          >
            {user.memberships.map((m) => (
              <option key={m.tenant_id} value={m.tenant_id}>
                {m.tenant_name} ({m.role})
              </option>
            ))}
          </select>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              style={({ isActive }) => ({
                padding: "8px 10px",
                borderRadius: 6,
                textDecoration: "none",
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                background: isActive ? "var(--surface-1)" : "transparent",
              })}
            >
              {item.label}
            </NavLink>
          ))}
          {user?.is_superadmin && (
            <NavLink
              to="/admin/tenants"
              style={({ isActive }) => ({
                padding: "8px 10px",
                borderRadius: 6,
                textDecoration: "none",
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                background: isActive ? "var(--surface-1)" : "transparent",
              })}
            >
              All tenants
            </NavLink>
          )}
        </div>

        <div style={{ marginTop: "auto", fontSize: 13, color: "var(--text-secondary)" }}>
          <div>{user?.email}</div>
          <button onClick={() => void logout()} style={{ marginTop: 8 }}>
            Log out
          </button>
        </div>
      </nav>

      <main style={{ flex: 1, padding: 24 }}>
        <Outlet />
      </main>
    </div>
  );
}
