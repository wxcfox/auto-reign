import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGuard } from "../AuthGuard";
import { isAuthenticated } from "@/lib/auth";

const navigationMocks = vi.hoisted(() => ({
  pathname: "/chat",
  searchParams: new URLSearchParams(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigationMocks.pathname,
  useSearchParams: () => navigationMocks.searchParams,
  useRouter: () => navigationMocks,
}));

vi.mock("@/lib/auth", () => ({
  isAuthenticated: vi.fn(),
}));

describe("AuthGuard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    navigationMocks.pathname = "/chat";
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
      expect(navigationMocks.replace).toHaveBeenCalledWith("/login?redirect=%2Fchat"),
    );
  });

  it("preserves query strings when redirecting private pages to login", async () => {
    navigationMocks.pathname = "/chat";
    navigationMocks.searchParams = new URLSearchParams("session=abc&mode=compact");
    vi.mocked(isAuthenticated).mockReturnValue(false);

    render(
      <AuthGuard>
        <div>Private workspace</div>
      </AuthGuard>,
    );

    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith(
        "/login?redirect=%2Fchat%3Fsession%3Dabc%26mode%3Dcompact",
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

  it("allows the setup page without a token", async () => {
    navigationMocks.pathname = "/setup";
    vi.mocked(isAuthenticated).mockReturnValue(false);

    render(
      <AuthGuard>
        <div>Administrator setup</div>
      </AuthGuard>,
    );

    expect(await screen.findByText("Administrator setup")).toBeInTheDocument();
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("does not treat the removed registration path as public", async () => {
    navigationMocks.pathname = "/register";
    vi.mocked(isAuthenticated).mockReturnValue(false);

    render(
      <AuthGuard>
        <div>Removed registration route</div>
      </AuthGuard>,
    );

    expect(screen.queryByText("Removed registration route")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith(
        "/login?redirect=%2Fregister",
      ),
    );
  });

  it("hides children synchronously when moving from a public page to a private location", async () => {
    const privateRender = vi.fn();
    function PrivateChat() {
      privateRender();
      return <div>Private chat</div>;
    }
    navigationMocks.pathname = "/login";
    vi.mocked(isAuthenticated).mockReturnValue(false);
    const { rerender } = render(
      <AuthGuard>
        <div>Login page</div>
      </AuthGuard>,
    );
    expect(screen.getByText("Login page")).toBeInTheDocument();

    navigationMocks.pathname = "/chat";
    navigationMocks.searchParams = new URLSearchParams("session=private");
    rerender(
      <AuthGuard>
        <PrivateChat />
      </AuthGuard>,
    );

    expect(privateRender).not.toHaveBeenCalled();
    expect(screen.queryByText("Private chat")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith(
        "/login?redirect=%2Fchat%3Fsession%3Dprivate",
      ),
    );
  });

  it("revalidates a new private location before rendering its children", async () => {
    const privateBRender = vi.fn();
    function PrivateB() {
      privateBRender();
      return <div>Private B</div>;
    }
    navigationMocks.pathname = "/private-a";
    vi.mocked(isAuthenticated).mockReturnValue(true);
    const { rerender } = render(
      <AuthGuard>
        <div>Private A</div>
      </AuthGuard>,
    );
    expect(await screen.findByText("Private A")).toBeInTheDocument();

    vi.mocked(isAuthenticated).mockReturnValue(false);
    navigationMocks.pathname = "/private-b";
    navigationMocks.searchParams = new URLSearchParams("tab=secret");
    rerender(
      <AuthGuard>
        <PrivateB />
      </AuthGuard>,
    );

    expect(privateBRender).not.toHaveBeenCalled();
    expect(screen.queryByText("Private B")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith(
        "/login?redirect=%2Fprivate-b%3Ftab%3Dsecret",
      ),
    );
  });

  it("does not reuse authorization when returning to the same private path", async () => {
    const loggedOutPrivateRender = vi.fn();
    function LoggedOutPrivateA() {
      loggedOutPrivateRender();
      return <div>Private A after logout</div>;
    }
    navigationMocks.pathname = "/private-a";
    vi.mocked(isAuthenticated).mockReturnValue(true);
    const { rerender } = render(
      <AuthGuard>
        <div>Private A</div>
      </AuthGuard>,
    );
    expect(await screen.findByText("Private A")).toBeInTheDocument();

    navigationMocks.pathname = "/login";
    rerender(
      <AuthGuard>
        <div>Login page</div>
      </AuthGuard>,
    );
    expect(screen.getByText("Login page")).toBeInTheDocument();

    vi.mocked(isAuthenticated).mockReturnValue(false);
    navigationMocks.pathname = "/private-a";
    rerender(
      <AuthGuard>
        <LoggedOutPrivateA />
      </AuthGuard>,
    );

    expect(loggedOutPrivateRender).not.toHaveBeenCalled();
    expect(screen.queryByText("Private A after logout")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith(
        "/login?redirect=%2Fprivate-a",
      ),
    );
  });
});
