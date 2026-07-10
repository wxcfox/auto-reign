import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "./page";
import {
  deleteWorkspaceArtifact,
  getHealth,
  getPreparationTasks,
  getWorkspaceFileContent,
  getWorkspaceFiles,
  getWorkspaceStatus,
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  deleteWorkspaceArtifact: vi.fn(),
  getHealth: vi.fn(),
  getPreparationTasks: vi.fn(),
  getWorkspaceArtifact: vi.fn(),
  getWorkspaceArtifacts: vi.fn(),
  getWorkspaceFileContent: vi.fn(),
  getWorkspaceFiles: vi.fn(),
  getWorkspaceStatus: vi.fn(),
}));

describe("DashboardPage", () => {
  const workspaceFilesResponse = {
    root: "workspace",
    directories: [
      {
        name: "workspace",
        relative_path: "",
        depth: 0,
        file_count: 2,
        child_directory_count: 3,
        created_at: "2026-07-09T08:00:00Z",
        updated_at: "2026-07-09T08:00:00Z",
        files: [
          {
            name: "manifest.md",
            relative_path: "manifest.md",
            directory: "",
            size_bytes: 1200,
            created_at: "2026-07-09T08:00:00Z",
            updated_at: "2026-07-09T08:00:00Z",
            owner: "workspace",
            kind: "manifest",
            processing_status: "completed",
            index_status: "completed",
            recovery_required: false,
            allowed_operations: ["replace_body"],
            artifact_id: "manifest-1",
            artifact_kind: "manifest",
          },
          {
            name: "workspace.md",
            relative_path: "workspace.md",
            directory: "",
            size_bytes: 520,
            created_at: "2026-07-09T08:00:00Z",
            updated_at: "2026-07-09T08:00:00Z",
            owner: "workspace",
            kind: "file",
            processing_status: "completed",
            index_status: "completed",
            recovery_required: false,
            allowed_operations: [],
            artifact_id: null,
            artifact_kind: null,
          },
        ],
      },
      {
        name: "practice",
        relative_path: "practice",
        depth: 1,
        file_count: 0,
        child_directory_count: 1,
        created_at: "2026-07-09T08:05:00Z",
        updated_at: "2026-07-09T08:05:00Z",
        files: [],
      },
      {
        name: "raw",
        relative_path: "raw",
        depth: 1,
        file_count: 2,
        child_directory_count: 0,
        created_at: "2026-07-09T08:01:00Z",
        updated_at: "2026-07-09T08:01:10Z",
        files: [
          {
            name: "resume.md",
            relative_path: "raw/resume.md",
            directory: "raw",
            size_bytes: 2300,
            created_at: "2026-07-09T08:01:00Z",
            updated_at: "2026-07-09T08:01:00Z",
            owner: "sources",
            kind: "source",
            processing_status: "completed",
            index_status: "completed",
            recovery_required: false,
            allowed_operations: [],
            artifact_id: "source-1",
            artifact_kind: "source",
          },
          {
            name: "resume.md.meta.json",
            relative_path: "raw/resume.md.meta.json",
            directory: "raw",
            size_bytes: 340,
            created_at: "2026-07-09T08:01:10Z",
            updated_at: "2026-07-09T08:01:10Z",
            owner: "workspace",
            kind: "file",
            processing_status: "completed",
            index_status: "completed",
            recovery_required: false,
            allowed_operations: [],
            artifact_id: null,
            artifact_kind: null,
          },
        ],
      },
      {
        name: "knowledge",
        relative_path: "knowledge",
        depth: 1,
        file_count: 1,
        child_directory_count: 0,
        created_at: "2026-07-09T08:03:00Z",
        updated_at: "2026-07-09T08:03:00Z",
        files: [
          {
            name: "redis.md",
            relative_path: "knowledge/redis.md",
            directory: "knowledge",
            size_bytes: 1800,
            created_at: "2026-07-09T08:03:00Z",
            updated_at: "2026-07-09T08:03:00Z",
            owner: "knowledge",
            kind: "knowledge",
            processing_status: "completed",
            index_status: "completed",
            recovery_required: false,
            allowed_operations: ["replace_body"],
            artifact_id: "knowledge-1",
            artifact_kind: "knowledge",
          },
        ],
      },
      {
        name: "2026",
        relative_path: "practice/2026",
        depth: 2,
        file_count: 0,
        child_directory_count: 1,
        created_at: "2026-07-09T08:06:00Z",
        updated_at: "2026-07-09T08:06:00Z",
        files: [],
      },
      {
        name: "07",
        relative_path: "practice/2026/07",
        depth: 3,
        file_count: 1,
        child_directory_count: 0,
        created_at: "2026-07-09T08:07:00Z",
        updated_at: "2026-07-09T08:07:00Z",
        files: [],
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("confirm", vi.fn(() => true));
    vi.mocked(deleteWorkspaceArtifact).mockResolvedValue({ id: "source-1", status: "deleted" });
    vi.mocked(getHealth).mockResolvedValue({
      status: "ok",
      storage: { mysql: "ok", qdrant: "ok" },
      providers: { openai: false, deepseek: false, qwen: true },
      workspace: { initialized: true },
    });
    vi.mocked(getWorkspaceStatus).mockResolvedValue({
      schema_version: 1,
      language: "zh-CN",
      artifact_count: 3,
      initialized: true,
    });
    vi.mocked(getPreparationTasks).mockResolvedValue({
      tasks: [],
    });
    vi.mocked(getWorkspaceFileContent).mockResolvedValue({
      name: "workspace.md",
      relative_path: "workspace.md",
      size_bytes: 520,
      updated_at: "2026-07-09T08:00:00Z",
      content: "# Auto Reign Workspace\n\nThis directory is managed by Auto Reign.",
    });
    vi.mocked(getWorkspaceFiles).mockResolvedValue(workspaceFilesResponse);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("mirrors the library layout for workspace child folders and files", async () => {
    const { container } = render(<DashboardPage />);

    expect(await screen.findByText("Interview learning workbench")).toBeInTheDocument();
    expect(getWorkspaceFiles).toHaveBeenCalledTimes(1);
    expect(getHealth).not.toHaveBeenCalled();
    expect(getWorkspaceStatus).not.toHaveBeenCalled();
    expect(getPreparationTasks).not.toHaveBeenCalled();

    expect(screen.queryByRole("button", { name: "workspace 2" })).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "raw 2" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(screen.getByRole("button", { name: "raw 2" })).getByText("2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "knowledge 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "practice 0" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "practice/2026 0" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "manifest.md" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "workspace.md" })).toBeInTheDocument();

    const fileTable = screen.getByRole("table", { name: "Workspace files" });
    const browser = container.querySelector(".workspace-browser");
    expect(screen.getByRole("button", { name: "Collapse folders" })).toBeInTheDocument();
    expect(browser).toHaveAttribute("data-categories-collapsed", "false");
    expect(within(fileTable).getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    expect(within(fileTable).getByRole("columnheader", { name: "Owner" })).toBeInTheDocument();
    expect(within(fileTable).getByRole("columnheader", { name: "Created" })).toBeInTheDocument();
    expect(within(fileTable).getByRole("columnheader", { name: "Updated" })).toBeInTheDocument();
    expect(within(fileTable).getByRole("columnheader", { name: "Actions" })).toBeInTheDocument();
    expect(within(fileTable).getByRole("link", { name: "resume.md" })).toHaveAttribute(
      "href",
      "/library/source-1",
    );
    expect(within(fileTable).getByText("raw/resume.md")).toBeInTheDocument();
    expect(within(fileTable).getByText("raw/resume.md.meta.json")).toBeInTheDocument();
    expect(within(fileTable).getByText("Sources")).toBeInTheDocument();
    expect(within(fileTable).getAllByText("Completed").length).toBeGreaterThan(0);
    expect(within(fileTable).getByLabelText("Edit resume.md")).toHaveAttribute(
      "href",
      "/library/source-1",
    );
    expect(within(fileTable).getByLabelText("Delete resume.md")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "manifest.md" }));

    expect(screen.getByRole("button", { name: "manifest.md" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(fileTable).getByRole("link", { name: "manifest.md" })).toHaveAttribute(
      "href",
      "/library/manifest-1",
    );
    expect(within(fileTable).queryByText("raw/resume.md")).not.toBeInTheDocument();
    expect(within(fileTable).queryByText("knowledge/redis.md")).not.toBeInTheDocument();
    expect(screen.queryByText("Current focus")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "practice 0" }));

    expect(within(fileTable).getByRole("button", { name: "2026" })).toBeInTheDocument();
    expect(within(fileTable).queryByText("raw/resume.md")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "workspace.md" }));

    await waitFor(() => expect(getWorkspaceFileContent).toHaveBeenCalledWith("workspace.md"));
    expect(screen.getByText("This directory is managed by Auto Reign.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse folders" }));

    expect(browser).toHaveAttribute("data-categories-collapsed", "true");
    expect(screen.getByRole("button", { name: "Expand folders" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "raw 2" })).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Workspace files" })).toBeInTheDocument();
  });

  it("deletes workspace artifact files through the same action pattern as the library", async () => {
    render(<DashboardPage />);

    const fileTable = await screen.findByRole("table", { name: "Workspace files" });
    fireEvent.click(within(fileTable).getByLabelText("Delete resume.md"));

    expect(window.confirm).toHaveBeenCalledWith('Delete "resume.md"? This removes the matching local workspace file.');
    await waitFor(() => expect(deleteWorkspaceArtifact).toHaveBeenCalledWith("source-1"));
    await waitFor(() => expect(getWorkspaceFiles).toHaveBeenCalledTimes(2));
  });
});
