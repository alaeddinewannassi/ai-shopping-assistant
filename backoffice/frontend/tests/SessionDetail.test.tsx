import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { SessionDetail } from "../src/pages/SessionDetail";
import { AuthProvider, SelectedTenantProvider } from "../src/lib/auth";
import { api } from "../src/lib/api";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    api: {
      me: vi.fn(),
      getSessionEvents: vi.fn(),
    },
  };
});

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/sessions/s1"]}>
        <AuthProvider>
          <SelectedTenantProvider>
            <Routes>
              <Route path="/sessions/:sessionId" element={<SessionDetail />} />
            </Routes>
          </SelectedTenantProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(api.me).mockResolvedValue({
    id: "1",
    email: "owner@example.com",
    name: "Owner",
    is_superadmin: false,
    memberships: [{ tenant_id: "t1", tenant_name: "T1", tenant_slug: "t1", role: "owner" }],
  });
});

describe("SessionDetail", () => {
  it("renders a rate_limited event as a plain sentence, not the raw exception it replaced", async () => {
    // Regression test for a real, confirmed live bug: a throttled LLM call used to log a raw
    // httpx exception string ("Client error '429 Too Many Requests' for url 'https://api...'
    // \nFor more information check: ...") as a generic "error" outcome — unreadable to an
    // admin as anything other than an unexplained failure, and long/unbroken enough to force
    // this page into horizontal scroll. llm_client.py now logs a distinct "rate_limited"
    // outcome with a short, clean reason instead.
    vi.mocked(api.getSessionEvents).mockResolvedValue([
      {
        turn_id: "turn-1",
        seq: 0,
        occurred_at: "2026-09-13T20:00:00Z",
        intent: "llm_call",
        action: "parse_turn",
        outcome: "rate_limited",
        details: {
          reason: "Groq free-tier rate limit reached for this model",
          retry_after_seconds: 2,
        },
        turn_elapsed_ms: null,
      },
    ]);

    renderPage();

    expect(
      await screen.findByText(/Groq free-tier rate limit reached for this model/),
    ).toBeInTheDocument();
    expect(screen.getByText(/retry after ~2s/)).toBeInTheDocument();
    expect(screen.queryByText(/429 Too Many Requests/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"reason":/)).not.toBeInTheDocument();
  });

  it("wraps a long unbroken details string instead of forcing the page to overflow", async () => {
    vi.mocked(api.getSessionEvents).mockResolvedValue([
      {
        turn_id: "turn-2",
        seq: 0,
        occurred_at: "2026-09-13T20:00:00Z",
        intent: "llm_call",
        action: "parse_turn",
        outcome: "error",
        details: { error: "x".repeat(300) },
        turn_elapsed_ms: null,
      },
    ]);

    renderPage();

    const pre = await screen.findByText(new RegExp("x".repeat(50)));
    expect(pre.tagName).toBe("PRE");
    expect(pre.style.whiteSpace).toBe("pre-wrap");
    expect(pre.style.overflowWrap).toBe("anywhere");
  });
});
