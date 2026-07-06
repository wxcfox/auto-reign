import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGuard } from "../AuthGuard";
import { isAuthenticated } from "@/lib/auth";

const navigationMocks = vi.hoisted(() => ({
  pathname: "/interview",
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigationMocks.pathname,
  useRouter: () => ({
    replace: navigationMocks.replace,
  }),
}));

vi.mock("@/lib/auth", () => ({
  isAuthenticated: vi.fn(),
}));

describe("AuthGuard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    navigationMocks.pathname = "/interview";
  });

  it("redirects private pages to login when no token exists", async () => {
    vi.mocked(isAuthenticated).mockReturnValue(false);

    render(
      <AuthGuard>
        <div>Private workspace</div>
      </AuthGuard>,
    );

    expect(screen.queryByText("Private workspace")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith("/login?redirect=%2Finterview"),
    );
  });

  it("renders private pages when a token exists", async () => {
    vi.mocked(isAuthenticated).mockReturnValue(true);

    render(
      <AuthGuard>
        <div>Private workspace</div>
      </AuthGuard>,
    );

    expect(await screen.findByText("Private workspace")).toBeInTheDocument();
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("allows public auth pages without a token", async () => {
    navigationMocks.pathname = "/login";
    vi.mocked(isAuthenticated).mockReturnValue(false);

    render(
      <AuthGuard>
        <div>Login page</div>
      </AuthGuard>,
    );

    expect(await screen.findByText("Login page")).toBeInTheDocument();
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });
});
