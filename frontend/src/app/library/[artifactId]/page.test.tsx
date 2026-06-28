import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ArtifactDetailPage from "./page";
import { getWorkspaceArtifact, replaceWorkspaceArtifactBody } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useParams: () => ({ artifactId: "knowledge-1" }),
}));

vi.mock("@/lib/api", () => ({
  getWorkspaceArtifact: vi.fn(),
  replaceWorkspaceArtifactBody: vi.fn(),
}));

describe("ArtifactDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getWorkspaceArtifact).mockResolvedValue({
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
      body: "# Redis\n\nCache stampede.",
    });
    vi.mocked(replaceWorkspaceArtifactBody).mockRejectedValue(new Error("not used"));
  });

  it("uses localized labels for the artifact editor chrome", async () => {
    render(<ArtifactDetailPage />);

    expect(await screen.findByRole("link", { name: /Back to library/i })).toHaveAttribute(
      "href",
      "/library",
    );
    expect(screen.getByText("Editable")).toBeInTheDocument();
    expect(screen.getByLabelText("Markdown body")).toHaveValue("# Redis\n\nCache stampede.");
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });
});
