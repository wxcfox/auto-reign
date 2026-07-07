import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGuard } from "../AuthGuard";
import { isAuthenticated } from "@/lib/auth";

const navigationMocks = vi.hoisted(() => ({
  pathname: "/interview",
  searchParams: new URLSearchParams(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigationMocks.pathname,
  useSearchParams: () => navigationMocks.searchParams,
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
    navigationMocks.searchParams = new URLSearchParams();
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

  it("preserves query strings when redirecting private pages to login", async () => {
    navigationMocks.pathname = "/interview";
    navigationMocks.searchParams = new URLSearchParams("session=abc&tab=review");
    vi.mocked(isAuthenticated).mockReturnValue(false);

    render(
      <AuthGuard>
        <div>Private workspace</div>
      </AuthGuard>,
    );

    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith(
        "/login?redirect=%2Finterview%3Fsession%3Dabc%26tab%3Dreview",
      ),
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
