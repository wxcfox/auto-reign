import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminUsersPage from "./page";
import i18next from "@/i18n/setup";
import {
  createAdminUser,
  getCurrentUser,
  listAdminUsers,
  resetAdminUserPassword,
  setAdminUserStatus,
} from "@/lib/api";
import type { AdminUser, User } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  createAdminUser: vi.fn(),
  getCurrentUser: vi.fn(),
  listAdminUsers: vi.fn(),
  resetAdminUserPassword: vi.fn(),
  setAdminUserStatus: vi.fn(),
}));

const currentUser: User = {
  id: 2,
  username: "reader",
  display_name: "Reader",
  role: "user",
  is_active: true,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const managedUser: AdminUser = { ...currentUser, role: "user" };

describe("admin users route", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18next.changeLanguage("en");
    vi.mocked(listAdminUsers).mockResolvedValue({ users: [managedUser] });
    vi.mocked(createAdminUser).mockResolvedValue(managedUser);
    vi.mocked(resetAdminUserPassword).mockResolvedValue(managedUser);
    vi.mocked(setAdminUserStatus).mockResolvedValue(managedUser);
  });

  it("mounts user management only after the real admin guard allows it", async () => {
    vi.mocked(getCurrentUser).mockResolvedValue({ ...currentUser, role: "admin" });
    render(<AdminUsersPage />);

    expect(screen.getByRole("status")).toHaveTextContent("Checking permissions…");
    expect(await screen.findByRole("heading", { name: "User management" })).toBeInTheDocument();
    expect(listAdminUsers).toHaveBeenCalledTimes(1);
  });

  it("does not mount the child or list users for an ordinary account", async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(currentUser);
    render(<AdminUsersPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Administrator access is required.",
    );
    expect(screen.queryByRole("heading", { name: "User management" })).not.toBeInTheDocument();
    expect(listAdminUsers).not.toHaveBeenCalled();
  });
});
