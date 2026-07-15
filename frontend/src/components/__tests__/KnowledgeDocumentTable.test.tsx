import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeDocumentTable } from "../KnowledgeDocumentTable";
import i18next from "@/i18n/setup";
import {
  deleteKnowledgeDocument,
  downloadKnowledgeDocument,
  listKnowledgeDocuments,
  readKnowledgeDocumentContent,
  reindexKnowledgeDocument,
} from "@/lib/api";
import type { KnowledgeDocument } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  deleteKnowledgeDocument: vi.fn(),
  downloadKnowledgeDocument: vi.fn(),
  listKnowledgeDocuments: vi.fn(),
  readKnowledgeDocumentContent: vi.fn(),
  reindexKnowledgeDocument: vi.fn(),
}));

const documentFixture: KnowledgeDocument = {
  id: "doc-1",
  collection_id: "collection-1",
  name: "guide.md",
  mime_type: "text/markdown",
  size_bytes: 7,
  status: "ready",
  index_generation: 1,
  error_code: null,
  error_message: null,
  is_active: true,
  indexed_at: "2026-07-13T00:01:00Z",
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:01:00Z",
};

function RemountingDetailHarness() {
  const [version, setVersion] = useState(0);
  return (
    <>
      <button onClick={() => setVersion((current) => current + 1)} type="button">
        Reload detail
      </button>
      <KnowledgeDocumentTable
        canManage
        collectionId="collection-1"
        key={version}
        onChanged={() => setVersion((current) => current + 1)}
      />
    </>
  );
}

describe("KnowledgeDocumentTable", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    i18next.changeLanguage("en");
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({ documents: [documentFixture] });
    vi.mocked(readKnowledgeDocumentContent).mockResolvedValue({
      document_id: documentFixture.id,
      content: "# Parsed guide",
    });
    vi.mocked(downloadKnowledgeDocument).mockResolvedValue(new Blob(["source"]));
    vi.mocked(reindexKnowledgeDocument).mockResolvedValue({
      ...documentFixture,
      status: "queued",
      index_generation: 2,
      indexed_at: null,
    });
    vi.mocked(deleteKnowledgeDocument).mockResolvedValue(null);
  });

  it("shows failed indexing and offers explicit reindex", async () => {
    const failed = {
      ...documentFixture,
      status: "failed" as const,
      error_message: "Document extraction failed.",
    };
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({ documents: [failed] });
    render(<KnowledgeDocumentTable collectionId="collection-1" canManage />);

    expect(await screen.findByText("Document extraction failed.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /reindex guide.md/i }));

    await waitFor(() =>
      expect(reindexKnowledgeDocument).toHaveBeenCalledWith("collection-1", "doc-1"),
    );
    expect(await screen.findByText("Queued")).toBeInTheDocument();
  });

  it("localizes safe embedding failure codes", async () => {
    const failed = {
      ...documentFixture,
      status: "failed" as const,
      error_code: "embedding_invalid_request",
      error_message: "Embedding provider rejected the request.",
    };
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({ documents: [failed] });

    render(<KnowledgeDocumentTable collectionId="collection-1" canManage />);

    expect(await screen.findByText("Embedding service rejected the request. Check the model configuration.")).toBeInTheDocument();
  });

  it("keeps cleanup_failed rows visible and retries explicit delete", async () => {
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({
      documents: [
        {
          ...documentFixture,
          is_active: false,
          error_code: "knowledge_cleanup_failed",
          error_message: "Knowledge cleanup failed.",
        },
      ],
    });
    render(<KnowledgeDocumentTable collectionId="collection-1" canManage />);

    expect(await screen.findByText("Knowledge cleanup failed.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry cleanup for guide.md/i }));

    await waitFor(() =>
      expect(deleteKnowledgeDocument).toHaveBeenCalledWith("collection-1", "doc-1"),
    );
    expect(await screen.findByText(/deleted and cleaned/i)).toBeInTheDocument();
    expect(screen.getByText(documentFixture.name)).toBeInTheDocument();
  });

  it("shows cleanup_pending after delete returns 202 without removing the row", async () => {
    vi.mocked(deleteKnowledgeDocument).mockResolvedValue({
      document_id: documentFixture.id,
      status: "cleanup_pending",
    });
    render(<RemountingDetailHarness />);

    fireEvent.click(await screen.findByRole("button", { name: /delete guide.md/i }));

    expect(await screen.findByText(/cleanup pending/i)).toBeInTheDocument();
    expect(screen.getByText(documentFixture.name)).toBeInTheDocument();
    expect(listKnowledgeDocuments).toHaveBeenCalledTimes(1);
  });

  it("keeps a completed 204 row until a reload confirms a cleaned tombstone", async () => {
    const cleanedTombstone: KnowledgeDocument = {
      ...documentFixture,
      is_active: false,
      error_code: null,
      error_message: null,
    };
    vi.mocked(listKnowledgeDocuments).mockReset();
    vi.mocked(listKnowledgeDocuments)
      .mockResolvedValueOnce({ documents: [documentFixture] })
      .mockResolvedValueOnce({ documents: [cleanedTombstone] });
    render(<RemountingDetailHarness />);

    fireEvent.click(await screen.findByRole("button", { name: /delete guide.md/i }));

    expect(await screen.findByText(/deleted and cleaned/i)).toBeInTheDocument();
    expect(screen.getByText(documentFixture.name)).toBeInTheDocument();
    expect(listKnowledgeDocuments).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /reload detail/i }));

    expect(await screen.findByText(/no documents/i)).toBeInTheDocument();
    expect(screen.queryByText(documentFixture.name)).not.toBeInTheDocument();
    expect(listKnowledgeDocuments).toHaveBeenCalledTimes(2);
  });

  it("uses manager visibility for inactive rows and public visibility for read-only rows", async () => {
    const manager = render(
      <KnowledgeDocumentTable collectionId="collection-1" canManage />,
    );
    await waitFor(() =>
      expect(listKnowledgeDocuments).toHaveBeenCalledWith("collection-1", {
        includeInactive: true,
      }),
    );
    manager.unmount();
    vi.mocked(listKnowledgeDocuments).mockClear();

    render(<KnowledgeDocumentTable collectionId="collection-1" />);
    await waitFor(() =>
      expect(listKnowledgeDocuments).toHaveBeenCalledWith("collection-1", {
        includeInactive: false,
      }),
    );
    expect(screen.queryByRole("button", { name: /delete guide.md/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reindex guide.md/i })).not.toBeInTheDocument();
  });

  it("previews authoritative parsed content", async () => {
    render(<KnowledgeDocumentTable collectionId="collection-1" />);

    fireEvent.click(await screen.findByRole("button", { name: /preview guide.md/i }));

    await waitFor(() =>
      expect(readKnowledgeDocumentContent).toHaveBeenCalledWith("collection-1", "doc-1"),
    );
    expect(await screen.findByText("# Parsed guide")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /close preview/i }));
    expect(screen.queryByText("# Parsed guide")).not.toBeInTheDocument();
  });

  it("downloads the original source with the document filename", async () => {
    const createObjectUrl = vi.fn(() => "blob:knowledge-source");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectUrl,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectUrl,
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<KnowledgeDocumentTable collectionId="collection-1" />);

    fireEvent.click(await screen.findByRole("button", { name: /download guide.md/i }));

    await waitFor(() =>
      expect(downloadKnowledgeDocument).toHaveBeenCalledWith("collection-1", "doc-1"),
    );
    expect(createObjectUrl).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:knowledge-source");
    click.mockRestore();
  });

  it("renders loading, empty, and recoverable error states", async () => {
    let resolveList!: (value: { documents: KnowledgeDocument[] }) => void;
    vi.mocked(listKnowledgeDocuments).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );
    const loading = render(<KnowledgeDocumentTable collectionId="collection-1" />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading documents/i);
    resolveList({ documents: [] });
    expect(await screen.findByText(/no documents/i)).toBeInTheDocument();
    loading.unmount();

    vi.mocked(listKnowledgeDocuments)
      .mockRejectedValueOnce(new Error("qdrant details"))
      .mockResolvedValueOnce({ documents: [] });
    render(<KnowledgeDocumentTable collectionId="collection-2" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load documents/i);
    expect(screen.queryByText(/qdrant details/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText(/no documents/i)).toBeInTheDocument();
  });

  it("keeps a row visible and reports resource_in_use on delete", async () => {
    const { ApiError } = await import("@/lib/api-error");
    vi.mocked(deleteKnowledgeDocument).mockRejectedValue(
      new ApiError("internal detail", { code: "resource_in_use", status: 409 }),
    );
    render(<KnowledgeDocumentTable collectionId="collection-1" canManage />);

    fireEvent.click(await screen.findByRole("button", { name: /delete guide.md/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/still referenced by an Agent/i);
    expect(screen.getByText(documentFixture.name)).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();
  });
});
