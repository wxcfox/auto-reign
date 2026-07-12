import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeUploader } from "../KnowledgeUploader";
import i18next from "@/i18n/setup";
import { uploadKnowledgeDocument } from "@/lib/api";
import type { KnowledgeDocument } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  uploadKnowledgeDocument: vi.fn(),
}));

const documentFixture: KnowledgeDocument = {
  id: "doc-1",
  collection_id: "collection-1",
  name: "guide.md",
  mime_type: "text/markdown",
  size_bytes: 7,
  status: "queued",
  index_generation: 1,
  error_code: null,
  error_message: null,
  is_active: true,
  indexed_at: null,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

describe("KnowledgeUploader", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    i18next.changeLanguage("en");
  });

  it("uploads one supported document to the selected collection", async () => {
    const onUploaded = vi.fn();
    vi.mocked(uploadKnowledgeDocument).mockResolvedValue(documentFixture);
    render(<KnowledgeUploader collectionId="collection-1" onUploaded={onUploaded} />);
    const file = new File(["# Guide"], "guide.md", { type: "text/markdown" });

    fireEvent.change(screen.getByLabelText(/upload document/i), {
      target: { files: [file] },
    });

    await waitFor(() =>
      expect(uploadKnowledgeDocument).toHaveBeenCalledWith("collection-1", file),
    );
    expect(await screen.findByText(/guide.md was uploaded/i)).toBeInTheDocument();
    expect(onUploaded).toHaveBeenCalledWith(documentFixture);
  });

  it("shows a stable upload error without using attachment draft behavior", async () => {
    vi.mocked(uploadKnowledgeDocument).mockRejectedValue(new Error("object key"));
    render(<KnowledgeUploader collectionId="collection-1" />);

    fireEvent.change(screen.getByLabelText(/upload document/i), {
      target: { files: [new File(["text"], "notes.txt", { type: "text/plain" })] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be uploaded/i);
    expect(screen.queryByText(/object key/i)).not.toBeInTheDocument();
  });

  it("disables upload when the collection is read-only", () => {
    render(<KnowledgeUploader collectionId="collection-1" disabled />);

    expect(screen.getByLabelText(/upload document/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /upload document/i })).toBeDisabled();
  });
});
