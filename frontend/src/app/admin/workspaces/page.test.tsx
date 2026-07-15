import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import GlobalWorkspacesPage from "./page";
import { RoleGuard } from "@/components/RoleGuard";
import { WorkspaceList } from "@/components/WorkspaceList";

vi.mock("@/components/RoleGuard", () => ({
  RoleGuard: vi.fn(({ children, role }: { children: ReactNode; role: string }) => (
    <div data-role={role}>{children}</div>
  )),
}));

vi.mock("@/components/WorkspaceList", () => ({
  WorkspaceList: vi.fn(({ scope }: { scope: string }) => (
    <div data-scope={scope}>Global Workspace management</div>
  )),
}));

describe("global workspaces route", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("guards global Workspace management and passes only global scope", () => {
    render(<GlobalWorkspacesPage />);

    expect(screen.getByText("Global Workspace management")).toHaveAttribute(
      "data-scope",
      "global",
    );
    expect(RoleGuard).toHaveBeenCalledWith(
      expect.objectContaining({ role: "admin" }),
      undefined,
    );
    expect(WorkspaceList).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "global" }),
      undefined,
    );
  });
});
