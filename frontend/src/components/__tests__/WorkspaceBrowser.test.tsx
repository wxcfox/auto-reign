import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceBrowser } from "../WorkspaceBrowser";
import {
  deleteWorkspaceFile,
  listWorkspaceFiles,
  readWorkspaceFile,
  writeWorkspaceFile,
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  deleteWorkspaceFile: vi.fn(),
  listWorkspaceFiles: vi.fn(),
  readWorkspaceFile: vi.fn(),
  writeWorkspaceFile: vi.fn(),
}));

describe("WorkspaceBrowser", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(listWorkspaceFiles).mockResolvedValue({
      directory: "",
      items: [
        {
          path: "notes",
          name: "notes",
          is_directory: true,
          size_bytes: null,
          etag: null,
        },
        {
          path: "AGENTS.md",
          name: "AGENTS.md",
          is_directory: false,
          size_bytes: 20,
          etag: "etag-1",
        },
        {
          path: "profile.md",
          name: "profile.md",
          is_directory: false,
          size_bytes: 10,
          etag: "etag-profile",
        },
      ],
    });
    vi.mocked(readWorkspaceFile).mockResolvedValue({
      path: "AGENTS.md",
      name: "AGENTS.md",
      is_directory: false,
      size_bytes: 20,
      etag: "etag-1",
      content: "# Rules",
    });
    vi.mocked(writeWorkspaceFile).mockResolvedValue({
      path: "AGENTS.md",
      name: "AGENTS.md",
      is_directory: false,
      size_bytes: 24,
      etag: "etag-2",
      content: "# Evolved",
    });
    vi.mocked(deleteWorkspaceFile).mockResolvedValue(undefined);
  });

  it("opens and saves a file with its ETag but never offers root AGENTS.md deletion", async () => {
    render(<WorkspaceBrowser scope="private" workspaceId="ws-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "AGENTS.md" }));
    fireEvent.change(await screen.findByRole("textbox", { name: /file content/i }), {
      target: { value: "# Evolved" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(writeWorkspaceFile).toHaveBeenCalledWith("private", "ws-1", {
        path: "AGENTS.md",
        content: "# Evolved",
        expected_etag: "etag-1",
      }),
    );
    expect(
      screen.queryByRole("button", { name: /delete AGENTS\.md/i }),
    ).not.toBeInTheDocument();
  });

  it("navigates direct child directories and back to the root", async () => {
    vi.mocked(listWorkspaceFiles).mockImplementation(async (_scope, _workspaceId, directory) => ({
      directory,
      items:
        directory === "notes"
          ? [
              {
                path: "notes/python.md",
                name: "python.md",
                is_directory: false,
                size_bytes: 12,
                etag: "etag-python",
              },
            ]
          : [
              {
                path: "notes",
                name: "notes",
                is_directory: true,
                size_bytes: null,
                etag: null,
              },
            ],
    }));
    render(<WorkspaceBrowser scope="private" workspaceId="ws-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "notes" }));
    await waitFor(() =>
      expect(listWorkspaceFiles).toHaveBeenLastCalledWith("private", "ws-1", "notes"),
    );
    expect(await screen.findByRole("button", { name: "python.md" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /workspace root/i }));
    await waitFor(() =>
      expect(listWorkspaceFiles).toHaveBeenLastCalledWith("private", "ws-1", ""),
    );
  });

  it("explicitly deletes ordinary files and reloads the directory", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<WorkspaceBrowser scope="global" workspaceId="ws-global" />);

    fireEvent.click(await screen.findByRole("button", { name: /delete profile\.md/i }));

    await waitFor(() =>
      expect(deleteWorkspaceFile).toHaveBeenCalledWith("global", "ws-global", "profile.md"),
    );
    await waitFor(() => expect(listWorkspaceFiles).toHaveBeenCalledTimes(2));
    confirm.mockRestore();
  });

  it("shows recoverable list failures without leaking exception details", async () => {
    vi.mocked(listWorkspaceFiles)
      .mockRejectedValueOnce(new Error("bucket secret"))
      .mockResolvedValueOnce({ directory: "", items: [] });
    render(<WorkspaceBrowser scope="private" workspaceId="ws-1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load files/i);
    expect(screen.queryByText(/bucket secret/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByText(/this folder is empty/i)).toBeInTheDocument();
  });

  it("resets navigation when the physical workspace identity changes", async () => {
    vi.mocked(listWorkspaceFiles).mockImplementation(
      async (_scope, workspaceId, directory) => ({
        directory,
        items:
          workspaceId === "ws-1" && directory === ""
            ? [
                {
                  path: "notes",
                  name: "notes",
                  is_directory: true,
                  size_bytes: null,
                  etag: null,
                },
              ]
            : [],
      }),
    );
    const view = render(<WorkspaceBrowser scope="private" workspaceId="ws-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "notes" }));
    await waitFor(() =>
      expect(listWorkspaceFiles).toHaveBeenLastCalledWith("private", "ws-1", "notes"),
    );

    view.rerender(<WorkspaceBrowser scope="private" workspaceId="ws-2" />);

    await waitFor(() =>
      expect(listWorkspaceFiles).toHaveBeenLastCalledWith("private", "ws-2", ""),
    );
    expect(screen.queryByRole("button", { name: "notes" })).not.toBeInTheDocument();
  });
});
