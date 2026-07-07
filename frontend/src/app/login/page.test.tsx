import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "./page";
import { loginUser } from "@/lib/api";
import { setAuthToken } from "@/lib/auth";
import type { User } from "@/lib/types";

const navigationMocks = vi.hoisted(() => ({
  replace: vi.fn(),
  searchParams: new URLSearchParams("redirect=/library"),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: navigationMocks.replace,
  }),
  useSearchParams: () => navigationMocks.searchParams,
}));

vi.mock("@/lib/api", () => ({
  loginUser: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  setAuthToken: vi.fn(),
}));

const user: User = {
  id: 1,
  username: "alice",
  display_name: "Alice",
  is_active: true,
  created_at: "2026-07-06T00:00:00Z",
  updated_at: "2026-07-06T00:00:00Z",
};

describe("LoginPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    navigationMocks.searchParams = new URLSearchParams("redirect=/library");
  });

  it("logs in and redirects to the requested page", async () => {
    vi.mocked(loginUser).mockResolvedValue({
      access_token: "token-1",
      token_type: "bearer",
      user,
    });

    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText(/Password/i), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Log in$/i }));

    await waitFor(() =>
      expect(loginUser).toHaveBeenCalledWith("alice", "correct horse battery staple"),
    );
    expect(setAuthToken).toHaveBeenCalledWith("token-1");
    expect(navigationMocks.replace).toHaveBeenCalledWith("/library");
  });

  it("shows an error when credentials are invalid", async () => {
    vi.mocked(loginUser).mockRejectedValue(new Error("invalid"));

    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /^Log in$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Username or password is incorrect.");
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });
});
