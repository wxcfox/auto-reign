import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LibraryPage from "./page";
import { deleteWorkspaceArtifact, getWorkspaceArtifacts } from "@/lib/api";

vi.mock("@/components/DocumentUploader", () => ({
  DocumentUploader: () => <button type="button">Upload</button>,
}));

vi.mock("@/lib/api", () => ({
  deleteWorkspaceArtifact: vi.fn(),
  getWorkspaceArtifacts: vi.fn(),
}));

const artifacts = [
  {
    id: "knowledge-1",
    kind: "knowledge",
    owner: "knowledge",
    relative_path: "knowledge/redis.md",
    display_name: "redis.md",
    revision: 2,
    processing_status: "completed",
    index_status: "completed",
    recovery_required: false,
    allowed_operations: ["replace_body"],
    created_at: "2026-06-22T10:00:00Z",
    updated_at: "2026-06-23T10:00:00Z",
  },
  {
    id: "source-1",
    kind: "source",
    owner: "sources",
    relative_path: "sources/documents/229ca53a-resume.md",
    display_name: "resume.md",
    revision: 1,
    processing_status: "completed",
    index_status: "completed",
    recovery_required: false,
    allowed_operations: [],
    created_at: "2026-06-22T09:00:00Z",
    updated_at: "2026-06-22T09:00:00Z",
  },
];

describe("LibraryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getWorkspaceArtifacts).mockResolvedValue({ artifacts });
    vi.mocked(deleteWorkspaceArtifact).mockResolvedValue({
      id: "knowledge-1",
      status: "deleted",
    });
  });

  it("groups library files by category before showing table rows", async () => {
    const { container } = render(<LibraryPage />);

    expect(await screen.findByRole("button", { name: /Knowledge cards\s+1/i })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Name/i })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Owner/i })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Created/i })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Updated/i })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Actions/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Rebuild index/i })).not.toBeInTheDocument();

    const sources = screen.getByRole("button", { name: /Sources\s+1/i });

    fireEvent.click(sources);

    expect(screen.getAllByText("resume.md").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "resume.md" })).toHaveAttribute("title", "resume.md");
    expect(container.querySelector(".library-table-scroll")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Preview/i)).not.toBeInTheDocument();
    expect(screen.queryByText("229ca53a-resume.md")).not.toBeInTheDocument();
    expect(screen.queryByText("knowledge/redis.md")).not.toBeInTheDocument();
  });

  it("places search and upload in the file list heading", async () => {
    const { container } = render(<LibraryPage />);

    expect(await screen.findByRole("button", { name: /Knowledge cards\s+1/i })).toBeInTheDocument();
    const fileHeading = container.querySelector(".library-file-heading");
    const searchbox = screen.getByRole("searchbox", { name: /Filter by keyword/i });
    const upload = screen.getByRole("button", { name: /Upload/i });

    expect(fileHeading).toContainElement(searchbox);
    expect(fileHeading).toContainElement(upload);
    expect(
      searchbox.compareDocumentPosition(upload) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(container.querySelector(".library-sidebar-tools")).toBeNull();
    expect(container.querySelector(".library-upload-panel")).toBeNull();
  });

  it("deletes a workspace artifact from the table action", async () => {
    vi.mocked(getWorkspaceArtifacts)
      .mockResolvedValueOnce({ artifacts })
      .mockResolvedValueOnce({ artifacts: artifacts.slice(1) });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<LibraryPage />);

    expect(await screen.findByRole("link", { name: "redis.md" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Delete redis\.md/i }));

    await waitFor(() => expect(deleteWorkspaceArtifact).toHaveBeenCalledWith("knowledge-1"));
    expect(await screen.findByText(/File deleted/i)).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("redis.md"));
  });
});
