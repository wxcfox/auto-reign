import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RoleGuard } from "../RoleGuard";
import i18next from "@/i18n/setup";
import { getCurrentUser } from "@/lib/api";
import type { User } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getCurrentUser: vi.fn(),
}));

const userFixture: User = {
  id: 2,
  username: "reader",
  display_name: "Reader",
  role: "user",
  is_active: true,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

describe("RoleGuard", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18next.changeLanguage("en");
  });

  it("does not render admin content for an ordinary user", async () => {
    vi.mocked(getCurrentUser).mockResolvedValue(userFixture);
    render(
      <RoleGuard role="admin">
        <div>Admin content</div>
      </RoleGuard>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Checking permissions…");
    expect(screen.queryByText("Admin content")).not.toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Administrator access is required.",
    );
  });

  it("renders admin content only after the current role is confirmed", async () => {
    vi.mocked(getCurrentUser).mockResolvedValue({ ...userFixture, role: "admin" });
    render(
      <RoleGuard role="admin">
        <div>Admin content</div>
      </RoleGuard>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Checking permissions…");
    expect(await screen.findByText("Admin content")).toBeInTheDocument();
  });

  it("shows a stable load error without exposing request details", async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(new Error("token and server details"));
    render(
      <RoleGuard role="admin">
        <div>Admin content</div>
      </RoleGuard>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Permissions could not be checked.",
    );
    expect(screen.queryByText(/token and server/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Admin content")).not.toBeInTheDocument();
  });

  it("uses explicit Chinese permissions resources", async () => {
    await i18next.changeLanguage("zh-CN");
    vi.mocked(getCurrentUser).mockResolvedValue(userFixture);
    render(
      <RoleGuard role="admin">
        <div>管理内容</div>
      </RoleGuard>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("正在检查权限…");
    expect(await screen.findByRole("alert")).toHaveTextContent("需要管理员权限。");
    expect(i18next.getResource("zh-CN", "common", "permissions.admin_required")).toBe(
      "需要管理员权限。",
    );
  });
});
