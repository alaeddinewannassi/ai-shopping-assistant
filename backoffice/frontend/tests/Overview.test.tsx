import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { Overview } from "../src/pages/Overview";
import { AuthProvider, SelectedTenantProvider } from "../src/lib/auth";
import { api } from "../src/lib/api";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    api: {
      me: vi.fn(),
      getOverview: vi.fn(),
      getTimeseries: vi.fn(),
    },
  };
});

const BASE_OVERVIEW = {
  session_count: 10,
  turn_count: 30,
  checkout_handed_off_count: 2,
  checkout_rate: 0.2,
  avg_turn_latency_ms: 500,
  p95_turn_latency_ms: 900,
  error_event_count: 0,
  error_rate: 0,
  llm_requests_limit: null,
  llm_requests_remaining: null,
  llm_tokens_limit: null,
  llm_tokens_remaining: null,
  llm_snapshot_at: null,
};

function renderPage() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <SelectedTenantProvider>
          <Overview />
        </SelectedTenantProvider>
      </AuthProvider>
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
  vi.mocked(api.getTimeseries).mockResolvedValue([]);
});

describe("Overview LLM capacity gauge", () => {
  it("renders live Groq headroom with a warning tone when running low", async () => {
    vi.mocked(api.getOverview).mockResolvedValue({
      ...BASE_OVERVIEW,
      llm_requests_limit: 1000,
      llm_requests_remaining: 993,
      llm_tokens_limit: 8000,
      llm_tokens_remaining: 500, // well under 30% headroom
      llm_snapshot_at: new Date(Date.now() - 5000).toISOString(),
    });

    renderPage();

    expect(await screen.findByText("LLM capacity")).toBeInTheDocument();
    expect(screen.getByText("993 / 1,000")).toBeInTheDocument();
    const tokensValue = screen.getByText("500 / 8,000");
    expect(tokensValue).toBeInTheDocument();
    expect(tokensValue.style.color).toBe("var(--critical)"); // 500/8000 = 6.25% < 10%
    expect(screen.getByText(/as of \d+s ago/)).toBeInTheDocument();
  });

  it("omits the LLM capacity card entirely when no turn in range made a real LLM call", async () => {
    vi.mocked(api.getOverview).mockResolvedValue(BASE_OVERVIEW);

    renderPage();

    await screen.findByText("Checkout rate");
    expect(screen.queryByText("LLM capacity")).not.toBeInTheDocument();
  });

  it("plots real LLM token consumption as a third trend toggle", async () => {
    vi.mocked(api.getOverview).mockResolvedValue(BASE_OVERVIEW);
    vi.mocked(api.getTimeseries).mockResolvedValue([
      { date: "2026-09-01", session_count: 1, turn_count: 2, llm_tokens: 321 },
    ]);
    const user = userEvent.setup();

    renderPage();

    await user.click(await screen.findByRole("button", { name: "LLM tokens" }));

    expect(await screen.findByRole("img", { name: "LLM tokens per day" })).toBeInTheDocument();
    expect(screen.getByText("321")).toBeInTheDocument();
  });
});
