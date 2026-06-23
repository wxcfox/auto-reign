import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LibraryPage from "./page";
import { getWorkspaceArtifacts, rebuildWorkspaceIndex } from "@/lib/api";

vi.mock("@/components/DocumentUploader", () => ({
  DocumentUploader: () => <div>Uploader</div>,
}));

vi.mock("@/lib/api", () => ({
  getWorkspaceArtifacts: vi.fn(),
  rebuildWorkspaceIndex: vi.fn(),
}));

const artifacts = [
  {
    id: "knowledge-1",
    kind: "knowledge",
    relative_path: "knowledge/redis.md",
    revision: 2,
    processing_status: "completed",
    index_status: "completed",
    recovery_required: false,
    allowed_operations: ["replace_body"],
  },
  {
    id: "source-1",
    kind: "source",
    relative_path: "sources/resume.md",
    revision: 1,
    processing_status: "completed",
    index_status: "completed",
    recovery_required: false,
    allowed_operations: [],
  },
];

describe("LibraryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getWorkspaceArtifacts).mockResolvedValue({ artifacts });
    vi.mocked(rebuildWorkspaceIndex).mockResolvedValue({
      status: "rebuilt",
      collection: "auto_reign",
    });
  });

  it("groups library files by category before showing files", async () => {
    render(<LibraryPage />);

    expect(await screen.findByRole("button", { name: /Knowledge cards\s+1/i })).toBeInTheDocument();
    const sources = screen.getByRole("button", { name: /Sources\s+1/i });

    fireEvent.click(sources);

    expect(screen.getAllByText("sources/resume.md").length).toBeGreaterThan(0);
    expect(screen.queryByText("knowledge/redis.md")).not.toBeInTheDocument();
  });
});
