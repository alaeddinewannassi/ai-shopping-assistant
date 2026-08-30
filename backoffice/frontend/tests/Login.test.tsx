import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { Login } from "../src/pages/Login";
import { AuthProvider } from "../src/lib/auth";
import { api, ApiError } from "../src/lib/api";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    api: {
      me: vi.fn(),
      login: vi.fn(),
    },
  };
});

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <Login />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(api.me).mockRejectedValue(new ApiError(401, "not authenticated"));
});

describe("Login page", () => {
  it("shows an error message when login fails", async () => {
    vi.mocked(api.login).mockRejectedValue(new ApiError(401, "Invalid email or password"));
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText(/email/i), "owner@example.com");
    await user.type(screen.getByLabelText(/password/i), "wrong-password");
    await user.click(screen.getByRole("button", { name: /log in/i }));

    await waitFor(() => expect(screen.getByText("Invalid email or password")).toBeInTheDocument());
  });

  it("calls api.login with the entered credentials", async () => {
    vi.mocked(api.login).mockResolvedValue({
      id: "1",
      email: "owner@example.com",
      name: "Owner",
      is_superadmin: false,
      memberships: [],
    });
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText(/email/i), "owner@example.com");
    await user.type(screen.getByLabelText(/password/i), "correct-password");
    await user.click(screen.getByRole("button", { name: /log in/i }));

    await waitFor(() =>
      expect(api.login).toHaveBeenCalledWith("owner@example.com", "correct-password"),
    );
  });
});
