import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WorkspacesPage from "./page";
import { WorkspaceList } from "@/components/WorkspaceList";

vi.mock("@/components/WorkspaceList", () => ({
  WorkspaceList: vi.fn(({ scope }: { scope: string }) => (
    <div data-scope={scope}>Workspace management</div>
  )),
}));

describe("personal workspaces route", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders private Workspace management without an admin guard", () => {
    render(<WorkspacesPage />);

    expect(screen.getByText("Workspace management")).toHaveAttribute(
      "data-scope",
      "private",
    );
    expect(WorkspaceList).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "private" }),
      undefined,
    );
  });
});
