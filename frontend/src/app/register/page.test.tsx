import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RegisterPage from "./page";
import { registerUser } from "@/lib/api";
import { setAuthToken } from "@/lib/auth";
import type { User } from "@/lib/types";

const navigationMocks = vi.hoisted(() => ({
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: navigationMocks.replace,
  }),
}));

vi.mock("@/lib/api", () => ({
  registerUser: vi.fn(),
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

describe("RegisterPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("creates a local user and opens the workspace", async () => {
    vi.mocked(registerUser).mockResolvedValue({
      access_token: "token-1",
      token_type: "bearer",
      user,
    });

    render(<RegisterPage />);
    fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText(/Password/i), {
      target: { value: "correct horse battery staple" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Create account$/i }));

    await waitFor(() =>
      expect(registerUser).toHaveBeenCalledWith("alice", "correct horse battery staple"),
    );
    expect(setAuthToken).toHaveBeenCalledWith("token-1");
    expect(navigationMocks.replace).toHaveBeenCalledWith("/");
  });

  it("requires a 12 character password", async () => {
    render(<RegisterPage />);
    fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: /^Create account$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Password must be at least 12 characters.",
    );
    expect(registerUser).not.toHaveBeenCalled();
  });
});
