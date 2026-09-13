import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, setSessionExpiredHandler } from "../src/lib/api";

/** Regression tests for a real, confirmed live bug: the access-token cookie expires after
 * 15 minutes (backend's _ACCESS_TOKEN_TTL) — a working POST /auth/refresh endpoint exists
 * to mint a new one from the 30-day refresh token, but request() never called it. Every
 * call just threw its 401 straight up, so any backoffice session older than 15 minutes saw
 * "Failed to load X" on every single page with no way to recover short of an accidental
 * page reload. */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  setSessionExpiredHandler(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("request() 401 handling", () => {
  it("silently refreshes and retries once when a request 401s", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        if (url.includes("/auth/refresh")) return jsonResponse(200, { status: "refreshed" });
        if (url.includes("/auth/me") && calls.filter((c) => c.includes("/auth/me")).length === 1) {
          return jsonResponse(401, { detail: "expired" });
        }
        return jsonResponse(200, {
          id: "1",
          email: "a@b.com",
          name: "A",
          is_superadmin: false,
          memberships: [],
        });
      }),
    );

    const user = await api.me();

    expect(user.email).toBe("a@b.com");
    expect(calls.filter((c) => c.includes("/auth/refresh")).length).toBe(1);
    expect(calls.filter((c) => c.includes("/auth/me")).length).toBe(2); // original + retry
  });

  it("dedupes concurrent 401s into a single refresh call", async () => {
    let refreshCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/auth/refresh")) {
          refreshCalls += 1;
          return jsonResponse(200, { status: "refreshed" });
        }
        // Every non-refresh call 401s exactly once per (path) — simulate by tracking retries
        // via a header-free trick: succeed on any call after the first refresh completes.
        if (refreshCalls === 0) return jsonResponse(401, { detail: "expired" });
        return jsonResponse(200, { session_count: 1, turn_count: 1 });
      }),
    );

    const [a, b] = await Promise.all([
      api.getOverview("t1", "2026-01-01", "2026-01-02"),
      api.getOverview("t1", "2026-01-01", "2026-01-02"),
    ]);

    expect(a).toBeTruthy();
    expect(b).toBeTruthy();
    expect(refreshCalls).toBe(1);
  });

  it("calls the session-expired handler and still throws when refresh itself fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/auth/refresh")) return jsonResponse(401, { detail: "refresh token expired" });
        return jsonResponse(401, { detail: "expired" });
      }),
    );
    const onExpired = vi.fn();
    setSessionExpiredHandler(onExpired);

    await expect(api.me()).rejects.toMatchObject({ status: 401 });
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("never retries the login endpoint itself (would recurse pointlessly)", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        return jsonResponse(401, { detail: "bad credentials" });
      }),
    );

    await expect(api.login("a@b.com", "wrong")).rejects.toMatchObject({ status: 401 });
    expect(calls.filter((c) => c.includes("/auth/login")).length).toBe(1);
    expect(calls.some((c) => c.includes("/auth/refresh"))).toBe(false);
  });
});
