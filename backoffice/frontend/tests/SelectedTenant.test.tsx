import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, vi, beforeEach } from "vitest";
import { AuthProvider, SelectedTenantProvider, useSelectedTenant } from "../src/lib/auth";
import { api } from "../src/lib/api";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    api: {
      me: vi.fn(),
      login: vi.fn(),
      logout: vi.fn(),
    },
  };
});

/** Stands in for AppShell's real <select> switcher — the exact shape of the bug doesn't
 * depend on AppShell's markup, only on there being one component that calls select(). */
function TenantSwitcher() {
  const [tenantId, select] = useSelectedTenant();
  return (
    <select aria-label="switcher" value={tenantId ?? ""} onChange={(e) => select(e.target.value)}>
      <option value="tenant-a">Tenant A</option>
      <option value="tenant-b">Tenant B</option>
    </select>
  );
}

/** Stands in for any analytics page (Overview/Funnel/Sessions/...) — a SEPARATE component
 * instance calling the same hook, the way each page does today. */
function TenantReader() {
  const [tenantId] = useSelectedTenant();
  return <p>Selected: {tenantId}</p>;
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.me).mockResolvedValue({
    id: "1",
    email: "owner@example.com",
    name: "Owner",
    is_superadmin: false,
    memberships: [
      { tenant_id: "tenant-a", tenant_name: "Tenant A", tenant_slug: "tenant-a", role: "owner" },
      { tenant_id: "tenant-b", tenant_name: "Tenant B", tenant_slug: "tenant-b", role: "owner" },
    ],
  });
});

describe("useSelectedTenant", () => {
  it("propagates a tenant switch to every consumer, not just the one that changed it", async () => {
    // Regression test for a real, confirmed live bug: switching tenants in the backoffice's
    // sidebar switcher (AppShell) never updated any analytics page's own numbers — Overview
    // kept showing whichever tenant was selected at the last full page load, no matter what
    // the switcher was set to, because useSelectedTenant used a private useState per call
    // site, only synchronized via localStorage at each component's own mount time. A
    // switcher-triggered select() updated ITS OWN copy and localStorage, but every
    // already-mounted page's own copy never re-rendered. This looked exactly like a
    // tenant-scoping bug in the backend/database (both tenants "showing the same numbers"),
    // but the underlying data was correct all along — confirmed by comparing a fresh page
    // load per tenant, which did show genuinely different numbers.
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <SelectedTenantProvider>
          <TenantSwitcher />
          <TenantReader />
        </SelectedTenantProvider>
      </AuthProvider>,
    );

    await screen.findByText("Selected: tenant-a"); // defaults to the first membership

    await user.selectOptions(screen.getByLabelText("switcher"), "tenant-b");

    await screen.findByText("Selected: tenant-b");
  });
});
