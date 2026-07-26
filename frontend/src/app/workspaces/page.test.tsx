import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WorkspacesPage from "./page";
import { WorkspaceList } from "@/components/WorkspaceList";

vi.mock("@/components/WorkspaceList", () => ({
  WorkspaceList: vi.fn(() => <div>Workspace management</div>),
}));

describe("workspaces route", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders one Workspace list for every role, with no scope to choose", () => {
    render(<WorkspacesPage />);

    expect(screen.getByText("Workspace management")).toBeInTheDocument();
    expect(WorkspaceList).toHaveBeenCalledWith({}, undefined);
  });
});
