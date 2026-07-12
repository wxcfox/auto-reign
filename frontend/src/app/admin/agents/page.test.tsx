import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GlobalAgentsPage from "./page";
import { AgentManagementPage } from "@/components/AgentManagementPage";
import { RoleGuard } from "@/components/RoleGuard";

vi.mock("@/components/AgentManagementPage", () => ({
  AgentManagementPage: vi.fn(({ scope }: { scope: string }) => (
    <div data-scope={scope}>Global Agent management</div>
  )),
}));

vi.mock("@/components/RoleGuard", () => ({
  RoleGuard: vi.fn(
    ({ children, role }: { children: React.ReactNode; role: string }) => (
      <div data-role={role}>{children}</div>
    ),
  ),
}));

describe("global agents route", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("wraps global Agent management in the admin role guard", () => {
    render(<GlobalAgentsPage />);

    expect(screen.getByText("Global Agent management")).toHaveAttribute(
      "data-scope",
      "global",
    );
    expect(RoleGuard).toHaveBeenCalledWith(
      expect.objectContaining({ role: "admin" }),
      undefined,
    );
    expect(AgentManagementPage).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "global" }),
      undefined,
    );
  });
});
