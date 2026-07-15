import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "./page";
import { getCurrentUser, loginUser } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { setAuthToken } from "@/lib/auth";
import type { AuthTokenResponse, User } from "@/lib/types";

const navigationMocks = vi.hoisted(() => ({
  replace: vi.fn(),
  searchParams: new URLSearchParams("redirect=/chat"),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMocks,
  useSearchParams: () => navigationMocks.searchParams,
}));

vi.mock("@/lib/api", () => ({
  getCurrentUser: vi.fn(),
  loginUser: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  setAuthToken: vi.fn(),
}));

const user: User = {
  id: 1,
  username: "alice",
  display_name: "Alice",
  role: "user",
  is_active: true,
  created_at: "2026-07-06T00:00:00Z",
  updated_at: "2026-07-06T00:00:00Z",
};

const tokenResponse: AuthTokenResponse = {
  access_token: "token-1",
  token_type: "bearer",
  user,
};

function enterCredentials() {
  fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: "alice" } });
  fireEvent.change(screen.getByLabelText(/Password/i), {
    target: { value: "correct horse battery staple" },
  });
}

function loginForm() {
  const form = screen.getByRole("button", { name: /^Log in$/i }).closest("form");
  expect(form).not.toBeNull();
  return form as HTMLFormElement;
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    navigationMocks.searchParams = new URLSearchParams("redirect=/chat");
    vi.mocked(getCurrentUser).mockRejectedValue(
      new ApiError("Authentication required.", {
        code: "auth_required",
        status: 401,
      }),
    );
  });

  it("redirects to setup when the backend requires an administrator password", async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(
      new ApiError("Setup required.", {
        code: "admin_password_setup_required",
        status: 400,
      }),
    );

    render(<LoginPage />);

    await waitFor(() => expect(navigationMocks.replace).toHaveBeenCalledWith("/setup"));
  });

  it("retries the setup probe once after a stale token is rejected", async () => {
    vi.mocked(getCurrentUser)
      .mockRejectedValueOnce(
        new ApiError("Token is invalid.", {
          code: "token_invalid",
          status: 401,
        }),
      )
      .mockRejectedValueOnce(
        new ApiError("Setup required.", {
          code: "admin_password_setup_required",
          status: 400,
        }),
      );

    render(<LoginPage />);

    await waitFor(() => expect(navigationMocks.replace).toHaveBeenCalledWith("/setup"));
    expect(getCurrentUser).toHaveBeenCalledTimes(2);
  });

  it("shares the bounded setup probe across Strict Mode effect replay", async () => {
    vi.mocked(getCurrentUser)
      .mockRejectedValueOnce(
        new ApiError("Token is invalid.", {
          code: "token_invalid",
          status: 401,
        }),
      )
      .mockRejectedValueOnce(
        new ApiError("Setup required.", {
          code: "admin_password_setup_required",
          status: 400,
        }),
      );

    render(
      <StrictMode>
        <LoginPage />
      </StrictMode>,
    );

    await waitFor(() => expect(navigationMocks.replace).toHaveBeenCalledWith("/setup"));
    expect(getCurrentUser).toHaveBeenCalledTimes(2);
  });

  it("stops after two ordinary unauthorized setup probes", async () => {
    render(<LoginPage />);

    await waitFor(() => expect(getCurrentUser).toHaveBeenCalledTimes(2));
    await Promise.resolve();
    expect(getCurrentUser).toHaveBeenCalledTimes(2);
    expect(navigationMocks.replace).not.toHaveBeenCalledWith("/setup");
    expect(screen.getByRole("heading", { name: /Log in to Auto Reign/i })).toBeInTheDocument();
  });

  it("does not redirect when the single retry fails with another error", async () => {
    vi.mocked(getCurrentUser)
      .mockRejectedValueOnce(
        new ApiError("Token is invalid.", {
          code: "token_invalid",
          status: 401,
        }),
      )
      .mockRejectedValueOnce(new Error("backend unavailable"));

    render(<LoginPage />);

    await waitFor(() => expect(getCurrentUser).toHaveBeenCalledTimes(2));
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("requires both the setup error code and status before redirecting", async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(
      new ApiError("Authentication required.", {
        code: "admin_password_setup_required",
        status: 401,
      }),
    );

    render(<LoginPage />);

    await waitFor(() => expect(getCurrentUser).toHaveBeenCalledTimes(2));
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("does not retry or redirect after an unmounted setup probe rejects", async () => {
    let rejectProbe: ((reason?: unknown) => void) | undefined;
    vi.mocked(getCurrentUser).mockReturnValue(
      new Promise((_, reject) => {
        rejectProbe = reject;
      }),
    );
    const { unmount } = render(<LoginPage />);

    unmount();
    rejectProbe?.(
      new ApiError("Token is invalid.", {
        code: "token_invalid",
        status: 401,
      }),
    );

    await Promise.resolve();
    expect(getCurrentUser).toHaveBeenCalledTimes(1);
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("logs in and redirects to the requested page", async () => {
    vi.mocked(loginUser).mockResolvedValue(tokenResponse);

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
    expect(navigationMocks.replace).toHaveBeenCalledWith("/chat");
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

  it("does not redirect a successful login back to setup", async () => {
    navigationMocks.searchParams = new URLSearchParams("redirect=/setup");
    vi.mocked(loginUser).mockResolvedValue({
      access_token: "token-1",
      token_type: "bearer",
      user,
    });

    render(<LoginPage />);
    fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: "alice" } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: /^Log in$/i }));

    await waitFor(() => expect(navigationMocks.replace).toHaveBeenCalledWith("/"));
  });

  it("prevents duplicate login submissions while the first request is pending", async () => {
    vi.mocked(loginUser).mockReturnValue(new Promise(() => undefined));
    render(<LoginPage />);
    enterCredentials();
    const form = loginForm();

    fireEvent.submit(form);
    fireEvent.submit(form);

    await waitFor(() => expect(loginUser).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: /Working/i })).toBeDisabled();
  });

  it("does not store a token or navigate when login resolves after unmount", async () => {
    let resolveLogin: ((response: AuthTokenResponse) => void) | undefined;
    vi.mocked(loginUser).mockReturnValue(
      new Promise((resolve) => {
        resolveLogin = resolve;
      }),
    );
    const { unmount } = render(<LoginPage />);
    enterCredentials();
    fireEvent.submit(loginForm());
    await waitFor(() => expect(loginUser).toHaveBeenCalledTimes(1));

    unmount();
    await act(async () => {
      resolveLogin?.(tokenResponse);
      await Promise.resolve();
    });

    expect(setAuthToken).not.toHaveBeenCalled();
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("does not show an error or navigate when login rejects after unmount", async () => {
    let rejectLogin: ((reason?: unknown) => void) | undefined;
    vi.mocked(loginUser).mockReturnValue(
      new Promise((_, reject) => {
        rejectLogin = reject;
      }),
    );
    const { unmount } = render(<LoginPage />);
    enterCredentials();
    fireEvent.submit(loginForm());
    await waitFor(() => expect(loginUser).toHaveBeenCalledTimes(1));

    unmount();
    await act(async () => {
      rejectLogin?.(new Error("invalid"));
      await Promise.resolve();
    });

    expect(setAuthToken).not.toHaveBeenCalled();
    expect(navigationMocks.replace).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not let a late setup probe override an in-progress successful login", async () => {
    let rejectProbe: ((reason?: unknown) => void) | undefined;
    let resolveLogin: ((response: AuthTokenResponse) => void) | undefined;
    vi.mocked(getCurrentUser).mockReturnValue(
      new Promise((_, reject) => {
        rejectProbe = reject;
      }),
    );
    vi.mocked(loginUser).mockReturnValue(
      new Promise((resolve) => {
        resolveLogin = resolve;
      }),
    );
    render(<LoginPage />);
    enterCredentials();
    fireEvent.submit(loginForm());
    await waitFor(() => expect(loginUser).toHaveBeenCalledTimes(1));

    await act(async () => {
      rejectProbe?.(
        new ApiError("Setup required.", {
          code: "admin_password_setup_required",
          status: 400,
        }),
      );
      await Promise.resolve();
    });
    expect(navigationMocks.replace).not.toHaveBeenCalledWith("/setup");

    await act(async () => {
      resolveLogin?.(tokenResponse);
      await Promise.resolve();
    });
    expect(setAuthToken).toHaveBeenCalledWith("token-1");
    expect(navigationMocks.replace).toHaveBeenCalledWith("/chat");
    expect(navigationMocks.replace).not.toHaveBeenCalledWith("/setup");
  });

  it("honors a deferred setup requirement after an in-progress login fails", async () => {
    let rejectProbe: ((reason?: unknown) => void) | undefined;
    let rejectLogin: ((reason?: unknown) => void) | undefined;
    vi.mocked(getCurrentUser).mockReturnValue(
      new Promise((_, reject) => {
        rejectProbe = reject;
      }),
    );
    vi.mocked(loginUser).mockReturnValue(
      new Promise((_, reject) => {
        rejectLogin = reject;
      }),
    );
    render(<LoginPage />);
    enterCredentials();
    fireEvent.submit(loginForm());
    await waitFor(() => expect(loginUser).toHaveBeenCalledTimes(1));

    await act(async () => {
      rejectProbe?.(
        new ApiError("Setup required.", {
          code: "admin_password_setup_required",
          status: 400,
        }),
      );
      await Promise.resolve();
    });
    expect(navigationMocks.replace).not.toHaveBeenCalled();

    await act(async () => {
      rejectLogin?.(new Error("invalid"));
      await Promise.resolve();
    });
    expect(navigationMocks.replace).toHaveBeenCalledWith("/setup");
  });

  it.each([
    ["an external URL", "https://evil.example/path"],
    ["a protocol-relative URL", "//evil.example/path"],
    ["an encoded backslash", "/%5Cevil.example"],
    ["a raw backslash", "/\\evil.example"],
    ["a multiply encoded backslash", "/%25255Cevil.example"],
    ["multiply encoded leading slashes", "/%252F%252Fevil.example"],
    ["the login page with a query", "/login?next=/chat"],
    ["the setup page with a hash", "/setup#password"],
    ["the login page with a trailing slash", "/login/"],
    ["a normalized login path", "/login/."],
    ["the setup page with a trailing slash", "/setup/"],
  ])("rejects %s as a post-login redirect", async (_label, redirect) => {
    navigationMocks.searchParams = new URLSearchParams({ redirect });
    vi.mocked(loginUser).mockResolvedValue(tokenResponse);
    render(<LoginPage />);
    enterCredentials();
    fireEvent.submit(loginForm());

    await waitFor(() => expect(navigationMocks.replace).toHaveBeenCalledWith("/"));
  });

  it("preserves a normalized internal redirect with query and hash", async () => {
    navigationMocks.searchParams = new URLSearchParams({
      redirect: "/agents?scope=mine#recent",
    });
    vi.mocked(loginUser).mockResolvedValue(tokenResponse);
    render(<LoginPage />);
    enterCredentials();
    fireEvent.submit(loginForm());

    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith("/agents?scope=mine#recent"),
    );
  });

  it("preserves percent escapes in a legitimate redirect query", async () => {
    navigationMocks.searchParams = new URLSearchParams({
      redirect: "/chat?q=100%25#x",
    });
    vi.mocked(loginUser).mockResolvedValue(tokenResponse);
    render(<LoginPage />);
    enterCredentials();
    fireEvent.submit(loginForm());

    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith("/chat?q=100%25#x"),
    );
  });
});
