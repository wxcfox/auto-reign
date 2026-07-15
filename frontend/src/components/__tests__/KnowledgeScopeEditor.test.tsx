import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeScopeEditor } from "../KnowledgeScopeEditor";
import i18next from "@/i18n/setup";
import { listKnowledgeDocuments } from "@/lib/api";
import type { KnowledgeCollection, KnowledgeDocument } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  listKnowledgeDocuments: vi.fn(),
}));

const collectionOne: KnowledgeCollection = {
  id: "collection-1",
  name: "Engineering handbook",
  scope: "private",
  can_manage: true,
  is_active: true,
  config: {
    chunk_size: 900,
    chunk_overlap: 120,
    top_k: 8,
    score_threshold: null,
  },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const collectionTwo: KnowledgeCollection = {
  ...collectionOne,
  id: "collection-2",
  name: "Support notes",
};

const documentFixture: KnowledgeDocument = {
  id: "doc-1",
  collection_id: collectionOne.id,
  name: "Guide.md",
  mime_type: "text/markdown",
  size_bytes: 100,
  status: "ready",
  index_generation: 1,
  error_code: null,
  error_message: null,
  is_active: true,
  indexed_at: "2026-07-13T00:01:00Z",
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:01:00Z",
};

describe("KnowledgeScopeEditor", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18next.changeLanguage("en");
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({ documents: [] });
  });

  it("adds multiple collections and loads documents only after subset mode is selected", async () => {
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({
      documents: [
        documentFixture,
        { ...documentFixture, id: "doc-2", name: "FAQ.pdf" },
      ],
    });
    const onChange = vi.fn();
    const { rerender } = render(
      <KnowledgeScopeEditor
        collections={[collectionOne, collectionTwo]}
        onChange={onChange}
        value={[]}
      />,
    );

    const addCollection = screen.getByLabelText("Add collection");
    fireEvent.change(addCollection, { target: { value: collectionOne.id } });
    fireEvent.click(screen.getByRole("button", { name: "Add knowledge scope" }));

    expect(onChange).toHaveBeenLastCalledWith([
      { collectionId: collectionOne.id, mode: "all", documentIds: [] },
    ]);
    expect(listKnowledgeDocuments).not.toHaveBeenCalled();

    rerender(
      <KnowledgeScopeEditor
        collections={[collectionOne, collectionTwo]}
        onChange={onChange}
        value={[{ collectionId: collectionOne.id, mode: "all", documentIds: [] }]}
      />,
    );
    fireEvent.click(screen.getByLabelText("Selected documents"));
    const subset = [
      { collectionId: collectionOne.id, mode: "subset" as const, documentIds: [] },
    ];
    expect(onChange).toHaveBeenLastCalledWith(subset);

    rerender(
      <KnowledgeScopeEditor
        collections={[collectionOne, collectionTwo]}
        onChange={onChange}
        value={subset}
      />,
    );
    expect(await screen.findByLabelText("Guide.md")).toBeInTheDocument();
    expect(listKnowledgeDocuments).toHaveBeenCalledTimes(1);
    expect(listKnowledgeDocuments).toHaveBeenCalledWith(collectionOne.id);

    rerender(
      <KnowledgeScopeEditor
        collections={[collectionOne, collectionTwo]}
        onChange={onChange}
        value={[{ collectionId: collectionOne.id, mode: "all", documentIds: [] }]}
      />,
    );
    rerender(
      <KnowledgeScopeEditor
        collections={[collectionOne, collectionTwo]}
        onChange={onChange}
        value={subset}
      />,
    );
    expect(await screen.findByLabelText("Guide.md")).toBeInTheDocument();
    expect(listKnowledgeDocuments).toHaveBeenCalledTimes(1);
  });

  it("deduplicates StrictMode loads and ignores a completed request after unmount", async () => {
    let resolveDocuments!: (value: { documents: KnowledgeDocument[] }) => void;
    vi.mocked(listKnowledgeDocuments).mockReturnValue(
      new Promise((resolve) => {
        resolveDocuments = resolve;
      }),
    );
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const view = render(
      <StrictMode>
        <KnowledgeScopeEditor
          collections={[collectionOne]}
          onChange={vi.fn()}
          value={[
            { collectionId: collectionOne.id, mode: "subset", documentIds: [] },
          ]}
        />
      </StrictMode>,
    );

    await waitFor(() => expect(listKnowledgeDocuments).toHaveBeenCalledTimes(1));
    view.unmount();
    resolveDocuments({ documents: [documentFixture] });
    await Promise.resolve();
    await Promise.resolve();

    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("renders a successful StrictMode request without issuing a duplicate", async () => {
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({
      documents: [documentFixture],
    });
    render(
      <StrictMode>
        <KnowledgeScopeEditor
          collections={[collectionOne]}
          onChange={vi.fn()}
          value={[
            { collectionId: collectionOne.id, mode: "subset", documentIds: [] },
          ]}
        />
      </StrictMode>,
    );

    expect(await screen.findByLabelText(documentFixture.name)).toBeInTheDocument();
    expect(listKnowledgeDocuments).toHaveBeenCalledTimes(1);
  });

  it("keeps errors, retries, and empty results isolated per collection", async () => {
    vi.mocked(listKnowledgeDocuments).mockImplementation(async (collectionId) => {
      if (collectionId === collectionOne.id) {
        throw new Error("offline detail must not render");
      }
      return {
        documents: [
          {
            ...documentFixture,
            id: "processing-doc",
            collection_id: collectionTwo.id,
            name: "Pending.pdf",
            status: "processing",
          },
        ],
      };
    });
    const onChange = vi.fn();
    render(
      <KnowledgeScopeEditor
        collections={[collectionOne, collectionTwo]}
        onChange={onChange}
        value={[
          { collectionId: collectionOne.id, mode: "subset", documentIds: [] },
          { collectionId: collectionTwo.id, mode: "subset", documentIds: [] },
        ]}
      />,
    );

    const firstCard = screen.getByRole("region", { name: collectionOne.name });
    const secondCard = screen.getByRole("region", { name: collectionTwo.name });
    expect(await within(firstCard).findByRole("alert")).toHaveTextContent(
      "Documents could not be loaded.",
    );
    expect(screen.queryByText(/offline detail/i)).not.toBeInTheDocument();
    expect(await within(secondCard).findByText("Pending.pdf")).toBeInTheDocument();
    expect(within(secondCard).getByText("Status: Processing")).toBeInTheDocument();
    expect(within(secondCard).queryByRole("alert")).not.toBeInTheDocument();

    vi.mocked(listKnowledgeDocuments).mockResolvedValueOnce({ documents: [] });
    fireEvent.click(within(firstCard).getByRole("button", { name: "Retry" }));

    expect(await within(firstCard).findByText("No documents in this collection.")).toBeInTheDocument();
    expect(listKnowledgeDocuments).toHaveBeenCalledTimes(3);
    expect(
      vi.mocked(listKnowledgeDocuments).mock.calls.filter(([id]) => id === collectionTwo.id),
    ).toHaveLength(1);
  });

  it("reports pending and missing selected documents and lets the user clear them", async () => {
    let resolveDocuments!: (value: { documents: KnowledgeDocument[] }) => void;
    vi.mocked(listKnowledgeDocuments).mockReturnValue(
      new Promise((resolve) => {
        resolveDocuments = resolve;
      }),
    );
    const onAvailabilityChange = vi.fn();
    const onChange = vi.fn();
    const unavailableDocumentId = "deleted-document-id";
    const value = [
      {
        collectionId: collectionOne.id,
        mode: "subset" as const,
        documentIds: [unavailableDocumentId],
      },
    ];
    const view = render(
      <KnowledgeScopeEditor
        collections={[collectionOne]}
        onAvailabilityChange={onAvailabilityChange}
        onChange={onChange}
        value={value}
      />,
    );

    await waitFor(() =>
      expect(onAvailabilityChange).toHaveBeenLastCalledWith(false),
    );
    expect(screen.queryByText(unavailableDocumentId)).not.toBeInTheDocument();

    resolveDocuments({ documents: [documentFixture] });
    expect(
      await screen.findByRole("alert", {
        name: `Selected document unavailable · ${unavailableDocumentId}`,
      }),
    ).toHaveTextContent(unavailableDocumentId);
    const unavailableDocument = screen.getByRole("checkbox", {
      name: `Unavailable selected document ${unavailableDocumentId}`,
    });
    expect(unavailableDocument).toBeChecked();
    expect(screen.getByLabelText(documentFixture.name)).toBeInTheDocument();
    expect(onAvailabilityChange).toHaveBeenLastCalledWith(false);

    fireEvent.click(unavailableDocument);
    expect(onChange).toHaveBeenLastCalledWith([
      {
        collectionId: collectionOne.id,
        mode: "subset",
        documentIds: [],
      },
    ]);
    view.rerender(
      <KnowledgeScopeEditor
        collections={[collectionOne]}
        onAvailabilityChange={onAvailabilityChange}
        onChange={onChange}
        value={[{ ...value[0], documentIds: [] }]}
      />,
    );
    await waitFor(() =>
      expect(onAvailabilityChange).toHaveBeenLastCalledWith(true),
    );
  });

  it("reports a failed subset document load as unavailable until using the full collection", async () => {
    vi.mocked(listKnowledgeDocuments).mockRejectedValue(new Error("offline details"));
    const onAvailabilityChange = vi.fn();
    const view = render(
      <KnowledgeScopeEditor
        collections={[collectionOne]}
        onAvailabilityChange={onAvailabilityChange}
        onChange={vi.fn()}
        value={[
          {
            collectionId: collectionOne.id,
            mode: "subset",
            documentIds: [documentFixture.id],
          },
        ]}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Documents could not be loaded.",
    );
    expect(onAvailabilityChange).toHaveBeenLastCalledWith(false);

    view.rerender(
      <KnowledgeScopeEditor
        collections={[collectionOne]}
        onAvailabilityChange={onAvailabilityChange}
        onChange={vi.fn()}
        value={[
          { collectionId: collectionOne.id, mode: "all", documentIds: [] },
        ]}
      />,
    );
    await waitFor(() =>
      expect(onAvailabilityChange).toHaveBeenLastCalledWith(true),
    );
  });

  it("shows every document status and localizes status labels", async () => {
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({
      documents: [
        { ...documentFixture, id: "doc-ready", name: "Ready.md", status: "ready" },
        { ...documentFixture, id: "doc-failed", name: "Failed.md", status: "failed" },
        {
          ...documentFixture,
          id: "doc-processing",
          name: "Processing.md",
          status: "processing",
        },
      ],
    });
    const { unmount } = render(
      <KnowledgeScopeEditor
        collections={[collectionOne]}
        onChange={vi.fn()}
        value={[{ collectionId: collectionOne.id, mode: "subset", documentIds: [] }]}
      />,
    );

    expect(await screen.findByText("Status: Ready")).toBeInTheDocument();
    expect(screen.getByText("Status: Failed")).toBeInTheDocument();
    expect(screen.getByText("Status: Processing")).toBeInTheDocument();
    unmount();

    await i18next.changeLanguage("zh-CN");
    render(
      <KnowledgeScopeEditor
        collections={[collectionOne]}
        onChange={vi.fn()}
        value={[{ collectionId: collectionOne.id, mode: "subset", documentIds: [] }]}
      />,
    );
    expect(await screen.findByText("状态：可用")).toBeInTheDocument();
    expect(screen.getByText("状态：失败")).toBeInTheDocument();
    expect(screen.getByText("状态：索引中")).toBeInTheDocument();
    expect(screen.queryByText(/^processing$/i)).not.toBeInTheDocument();
  });

  it("blocks stale duplicate candidates and limits scopes to twenty", () => {
    const collections = Array.from({ length: 21 }, (_, index) => ({
      ...collectionOne,
      id: `collection-${index + 1}`,
      name: `Collection ${index + 1}`,
    }));
    const value = collections.slice(0, 20).map((collection) => ({
      collectionId: collection.id,
      mode: "all" as const,
      documentIds: [],
    }));
    const onChange = vi.fn();
    const { rerender } = render(
      <KnowledgeScopeEditor collections={collections} onChange={onChange} value={value} />,
    );

    fireEvent.change(screen.getByLabelText("Add collection"), {
      target: { value: collections[20].id },
    });
    expect(screen.getByRole("button", { name: "Add knowledge scope" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Add knowledge scope" }));
    expect(onChange).not.toHaveBeenCalled();

    const nineteen = value.slice(0, 19);
    rerender(
      <KnowledgeScopeEditor collections={collections} onChange={onChange} value={nineteen} />,
    );
    fireEvent.change(screen.getByLabelText("Add collection"), {
      target: { value: collections[19].id },
    });
    rerender(
      <KnowledgeScopeEditor
        collections={collections}
        onChange={onChange}
        value={[...nineteen, value[19]]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add knowledge scope" }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("limits each document subset to one hundred unique documents", async () => {
    const documents = Array.from({ length: 101 }, (_, index) => ({
      ...documentFixture,
      id: `doc-${index + 1}`,
      name: `Document ${index + 1}`,
    }));
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({ documents });
    const selected = documents.slice(0, 100).map((document) => document.id);
    const onChange = vi.fn();
    render(
      <KnowledgeScopeEditor
        collections={[collectionOne]}
        onChange={onChange}
        value={[
          { collectionId: collectionOne.id, mode: "subset", documentIds: selected },
        ]}
      />,
    );

    expect(await screen.findByLabelText("Document 101")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Document 101"));
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("Document 1"));
    expect(onChange).toHaveBeenLastCalledWith([
      {
        collectionId: collectionOne.id,
        mode: "subset",
        documentIds: selected.slice(1),
      },
    ]);
  });

  it("uses the Chinese agents namespace without English fallback", async () => {
    await i18next.changeLanguage("zh-CN");
    render(
      <KnowledgeScopeEditor
        collections={[collectionOne]}
        onChange={vi.fn()}
        value={[]}
      />,
    );

    expect(screen.getByRole("group", { name: "资料库" })).toBeInTheDocument();
    expect(screen.getByLabelText("添加资料库")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加资料库范围" })).toBeInTheDocument();
    expect(screen.queryByText("Add knowledge scope")).not.toBeInTheDocument();
    expect(i18next.getResource("zh-CN", "agents", "knowledge.add_scope")).toBe(
      "添加资料库范围",
    );
  });
});
