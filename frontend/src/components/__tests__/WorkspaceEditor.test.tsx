import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceEditor } from "../WorkspaceEditor";
import { readWorkspaceFile, writeWorkspaceFile } from "@/lib/api";
import { ApiError } from "@/lib/api-error";

vi.mock("@/lib/api", () => ({
  readWorkspaceFile: vi.fn(),
  writeWorkspaceFile: vi.fn(),
}));

describe("WorkspaceEditor", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("preserves the draft after an ETag conflict and reloads only when requested", async () => {
    vi.mocked(readWorkspaceFile)
      .mockResolvedValueOnce({
        path: "notes/profile.md",
        name: "profile.md",
        is_directory: false,
        size_bytes: 3,
        etag: "etag-1",
        content: "Old",
      })
      .mockResolvedValueOnce({
        path: "notes/profile.md",
        name: "profile.md",
        is_directory: false,
        size_bytes: 6,
        etag: "etag-2",
        content: "Remote",
      });
    vi.mocked(writeWorkspaceFile).mockRejectedValue(
      new ApiError("Changed", { code: "workspace_conflict", status: 409 }),
    );
    render(
      <WorkspaceEditor scope="private" workspaceId="ws-1" path="notes/profile.md" />,
    );

    const editor = await screen.findByRole("textbox", { name: /file content/i });
    fireEvent.change(editor, { target: { value: "My local draft" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/changed since you opened it/i);
    expect(editor).toHaveValue("My local draft");
    expect(writeWorkspaceFile).toHaveBeenCalledWith("private", "ws-1", {
      path: "notes/profile.md",
      content: "My local draft",
      expected_etag: "etag-1",
    });

    fireEvent.click(screen.getByRole("button", { name: /reload/i }));
    await waitFor(() => expect(editor).toHaveValue("Remote"));
    expect(readWorkspaceFile).toHaveBeenCalledTimes(2);
  });

  it("uses the global scope and exposes a recoverable load error", async () => {
    vi.mocked(readWorkspaceFile)
      .mockRejectedValueOnce(new Error("driver details"))
      .mockResolvedValueOnce({
        path: "AGENTS.md",
        name: "AGENTS.md",
        is_directory: false,
        size_bytes: 7,
        etag: "etag-1",
        content: "# Rules",
      });
    render(<WorkspaceEditor scope="global" workspaceId="ws-global" path="AGENTS.md" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load the file/i);
    expect(screen.queryByText(/driver details/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByRole("textbox", { name: /file content/i })).toHaveValue("# Rules");
    expect(readWorkspaceFile).toHaveBeenLastCalledWith("global", "ws-global", "AGENTS.md");
  });
});
