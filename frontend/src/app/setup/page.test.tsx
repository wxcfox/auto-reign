import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SetupPage from "./page";
import { setupAdminPassword } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { setAuthToken } from "@/lib/auth";
import type { AuthTokenResponse } from "@/lib/types";

const navigationMocks = vi.hoisted(() => ({
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: navigationMocks.replace,
  }),
}));

vi.mock("@/lib/api", () => ({
  setupAdminPassword: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  setAuthToken: vi.fn(),
}));

const adminTokenResponse: AuthTokenResponse = {
  access_token: "admin-token",
  token_type: "bearer",
  user: {
    id: 1,
    username: "admin",
    display_name: "Administrator",
    role: "admin",
    is_active: true,
    created_at: "2026-07-13T00:00:00Z",
    updated_at: "2026-07-13T00:00:00Z",
  },
};

function enterPasswords(password: string, confirmation = password) {
  fireEvent.change(screen.getByLabelText(/New password/i), {
    target: { value: password },
  });
  fireEvent.change(screen.getByLabelText(/Confirm password/i), {
    target: { value: confirmation },
  });
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: /Set administrator password/i }));
}

describe("SetupPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("sets the initial administrator password and stores the returned token", async () => {
    vi.mocked(setupAdminPassword).mockResolvedValue(adminTokenResponse);
    render(<SetupPage />);

    enterPasswords("correct horse battery staple");
    submit();

    await waitFor(() =>
      expect(setupAdminPassword).toHaveBeenCalledWith("correct horse battery staple"),
    );
    expect(setAuthToken).toHaveBeenCalledWith("admin-token");
    expect(navigationMocks.replace).toHaveBeenCalledWith("/chat");
  });

  it("rejects passwords shorter than the backend minimum", async () => {
    render(<SetupPage />);

    enterPasswords("short");
    submit();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Password must be at least 6 characters.",
    );
    expect(setupAdminPassword).not.toHaveBeenCalled();
  });

  it("requires matching passwords", async () => {
    render(<SetupPage />);

    enterPasswords("secret-one", "secret-two");
    submit();

    expect(await screen.findByRole("alert")).toHaveTextContent("Passwords do not match.");
    expect(setupAdminPassword).not.toHaveBeenCalled();
  });

  it("enforces the backend maximum password length defensively", async () => {
    render(<SetupPage />);
    const password = "x".repeat(257);
    const newPasswordInput = screen.getByLabelText(/New password/i);
    const confirmationInput = screen.getByLabelText(/Confirm password/i);

    expect(newPasswordInput).toHaveAttribute("maxlength", "256");
    expect(confirmationInput).toHaveAttribute("maxlength", "256");
    fireEvent.change(newPasswordInput, { target: { value: password } });
    fireEvent.change(confirmationInput, { target: { value: password } });
    fireEvent.submit(newPasswordInput.closest("form") as HTMLFormElement);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Password must be no more than 256 characters.",
    );
    expect(setupAdminPassword).not.toHaveBeenCalled();
  });

  it("returns to login when the administrator password is already initialized", async () => {
    vi.mocked(setupAdminPassword).mockRejectedValue(
      new ApiError("Internal account state must stay private.", {
        code: "admin_password_already_initialized",
        status: 409,
      }),
    );
    render(<SetupPage />);

    enterPasswords("correct horse battery staple");
    submit();

    await waitFor(() => expect(navigationMocks.replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("Internal account state must stay private.")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a localized error for other setup failures", async () => {
    vi.mocked(setupAdminPassword).mockRejectedValue(new Error("network unavailable"));
    render(<SetupPage />);

    enterPasswords("correct horse battery staple");
    submit();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The administrator password could not be set.",
    );
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("prevents duplicate setup submissions while the first request is pending", async () => {
    vi.mocked(setupAdminPassword).mockReturnValue(new Promise(() => undefined));
    render(<SetupPage />);

    enterPasswords("correct horse battery staple");
    const form = screen.getByRole("button", { name: /Set administrator password/i }).closest(
      "form",
    );
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);
    fireEvent.submit(form as HTMLFormElement);

    await waitFor(() => expect(setupAdminPassword).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: /Working/i })).toBeDisabled();
  });

  it("does not store a token or navigate when setup resolves after unmount", async () => {
    let resolveSetup: ((response: AuthTokenResponse) => void) | undefined;
    vi.mocked(setupAdminPassword).mockReturnValue(
      new Promise((resolve) => {
        resolveSetup = resolve;
      }),
    );
    const { unmount } = render(<SetupPage />);
    enterPasswords("correct horse battery staple");
    submit();
    await waitFor(() => expect(setupAdminPassword).toHaveBeenCalledTimes(1));

    unmount();
    await act(async () => {
      resolveSetup?.(adminTokenResponse);
      await Promise.resolve();
    });

    expect(setAuthToken).not.toHaveBeenCalled();
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("does not navigate when setup rejects after unmount", async () => {
    let rejectSetup: ((reason?: unknown) => void) | undefined;
    vi.mocked(setupAdminPassword).mockReturnValue(
      new Promise((_, reject) => {
        rejectSetup = reject;
      }),
    );
    const { unmount } = render(<SetupPage />);
    enterPasswords("correct horse battery staple");
    submit();
    await waitFor(() => expect(setupAdminPassword).toHaveBeenCalledTimes(1));

    unmount();
    await act(async () => {
      rejectSetup?.(
        new ApiError("Already initialized.", {
          code: "admin_password_already_initialized",
          status: 409,
        }),
      );
      await Promise.resolve();
    });

    expect(setAuthToken).not.toHaveBeenCalled();
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });
});
