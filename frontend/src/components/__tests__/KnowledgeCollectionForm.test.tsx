import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
  KnowledgeCollectionForm,
} from "../KnowledgeCollectionForm";
import i18next from "@/i18n/setup";
import {
  createKnowledgeCollection,
  updateKnowledgeCollection,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { KnowledgeCollection, KnowledgeCollectionConfig } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  createKnowledgeCollection: vi.fn(),
  updateKnowledgeCollection: vi.fn(),
}));

const collection: KnowledgeCollection = {
  id: "collection-1",
  name: "My manuals",
  scope: "private",
  can_manage: true,
  config: { ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG },
  is_active: false,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

function setValue(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function fillConfig(config: {
  chunkSize: string;
  overlap: string;
  topK: string;
  threshold: string;
}) {
  setValue(/^chunk size$/i, config.chunkSize);
  setValue(/^chunk overlap$/i, config.overlap);
  setValue(/^top k$/i, config.topK);
  setValue(/^score threshold/i, config.threshold);
}

describe("KnowledgeCollectionForm", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18next.changeLanguage("en");
  });

  it("creates through the selected scope with Elasticsearch vector defaults", async () => {
    const onSaved = vi.fn();
    vi.mocked(createKnowledgeCollection).mockResolvedValue({
      ...collection,
      scope: "global",
      is_active: true,
    });
    render(<KnowledgeCollectionForm scope="global" onSaved={onSaved} />);

    setValue(/^name$/i, "  Shared manuals  ");
    fireEvent.click(screen.getByRole("button", { name: /create knowledge base/i }));

    await waitFor(() =>
      expect(createKnowledgeCollection).toHaveBeenCalledWith("global", {
        name: "Shared manuals",
        config: DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
      }),
    );
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("combobox", { name: /visibility|owner|scope/i }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /^retriever$/i }))
      .toHaveValue("elasticsearch");
    expect(screen.getByTestId("knowledge-retriever-select")).toBeEnabled();
    expect(screen.getByRole("combobox", { name: /retrieval mode/i })).toHaveValue("vector");
    expect(screen.getByText(/queues active documents for reindexing/i))
      .toBeInTheDocument();
    expect(screen.getByText(/without rebuilding the index/i))
      .toBeInTheDocument();
  });

  it("edits every numeric setting and preserves the collection lifecycle state", async () => {
    const updatedConfig: KnowledgeCollectionConfig = {
      ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
      chunk_size: 1_200,
      chunk_overlap: 300,
      top_k: 10,
      score_threshold: 0.25,
      retrieval_mode: "hybrid",
      vector_weight: 0.6,
      keyword_weight: 0.4,
    };
    vi.mocked(updateKnowledgeCollection).mockResolvedValue({
      ...collection,
      config: updatedConfig,
    });
    render(<KnowledgeCollectionForm collection={collection} scope="private" />);

    setValue(/^name$/i, "Updated manuals");
    fireEvent.change(screen.getByRole("combobox", { name: /retrieval mode/i }), {
      target: { value: "hybrid" },
    });
    setValue(/vector weight/i, "0.6");
    setValue(/keyword weight/i, "0.4");
    fillConfig({ chunkSize: "1200", overlap: "300", topK: "10", threshold: "0.25" });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(updateKnowledgeCollection).toHaveBeenCalledWith(
        "private",
        collection.id,
        {
          name: "Updated manuals",
          config: updatedConfig,
          is_active: false,
        },
      ),
    );
    expect(screen.queryByRole("checkbox", { name: /active|启用/i })).not.toBeInTheDocument();
  });

  it("shows an immutable Retriever in edit mode with localized guidance", async () => {
    render(<KnowledgeCollectionForm collection={collection} scope="private" />);

    expect(screen.getByTestId("knowledge-retriever-select")).toBeDisabled();
    expect(screen.getByTestId("knowledge-retriever-immutable-hint"))
      .toHaveTextContent(
        "Retriever is selected when the knowledge base is created and cannot be changed later.",
      );
    expect(screen.getByRole("combobox", { name: /retrieval mode/i })).toBeEnabled();

    await act(async () => {
      await i18next.changeLanguage("zh-CN");
    });
    expect(screen.getByTestId("knowledge-retriever-immutable-hint"))
      .toHaveTextContent("Retriever 在资料库创建时确定，创建后不可修改。");
  });

  it("keeps a Qdrant Retriever read-only and exposes vector mode only", () => {
    render(
      <KnowledgeCollectionForm
        collection={{
          ...collection,
          config: {
            ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
            retriever_type: "qdrant",
          },
        }}
        scope="private"
      />,
    );

    expect(screen.getByTestId("knowledge-retriever-select"))
      .toHaveValue("qdrant");
    expect(screen.getByTestId("knowledge-retriever-select")).toBeDisabled();
    expect(screen.getByRole("combobox", { name: /retrieval mode/i }))
      .toHaveValue("vector");
    expect(screen.queryByRole("option", { name: /keyword/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /hybrid/i })).not.toBeInTheDocument();
  });

  it("rejects non-integers, every out-of-range field, and overlap above half the chunk size", () => {
    render(<KnowledgeCollectionForm scope="private" />);
    setValue(/^name$/i, "Invalid config");

    fillConfig({ chunkSize: "199", overlap: "120", topK: "5", threshold: "0.5" });
    fireEvent.click(screen.getByRole("button", { name: /create knowledge base/i }));
    expect(screen.getByText(/integer from 200 to 4000/i)).toBeInTheDocument();

    fillConfig({ chunkSize: "200", overlap: "101", topK: "5", threshold: "0.5" });
    fireEvent.click(screen.getByRole("button", { name: /create knowledge base/i }));
    expect(screen.getByText(/cannot exceed half/i)).toBeInTheDocument();

    fillConfig({ chunkSize: "200.5", overlap: "100", topK: "31", threshold: "2" });
    fireEvent.click(screen.getByRole("button", { name: /create knowledge base/i }));
    expect(screen.getByText(/integer from 200 to 4000/i)).toBeInTheDocument();
    expect(screen.getByText(/integer from 1 to 10/i)).toBeInTheDocument();
    expect(screen.getByText(/number from 0 to 1/i)).toBeInTheDocument();
    expect(createKnowledgeCollection).not.toHaveBeenCalled();
  });

  it.each([
    {
      label: "minimum",
      values: { chunkSize: "200", overlap: "100", topK: "1", threshold: "0" },
      expected: {
        ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
        chunk_size: 200,
        chunk_overlap: 100,
        top_k: 1,
        score_threshold: 0,
      },
    },
    {
      label: "maximum",
      values: { chunkSize: "4000", overlap: "1000", topK: "10", threshold: "1" },
      expected: {
        ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
        chunk_size: 4000,
        chunk_overlap: 1000,
        top_k: 10,
        score_threshold: 1,
      },
    },
  ])("accepts the $label inclusive boundaries", async ({ values, expected }) => {
    vi.mocked(createKnowledgeCollection).mockResolvedValue({
      ...collection,
      is_active: true,
      config: expected,
    });
    render(<KnowledgeCollectionForm scope="private" />);
    setValue(/^name$/i, "Boundary config");
    fillConfig(values);

    fireEvent.click(screen.getByRole("button", { name: /create knowledge base/i }));

    await waitFor(() =>
      expect(createKnowledgeCollection).toHaveBeenCalledWith("private", {
        name: "Boundary config",
        config: expected,
      }),
    );
  });

  it("keeps the draft and localizes a resource name conflict", async () => {
    vi.mocked(createKnowledgeCollection).mockRejectedValue(
      new ApiError("internal detail", { code: "resource_name_taken", status: 409 }),
    );
    render(<KnowledgeCollectionForm scope="private" />);
    setValue(/^name$/i, "My references");

    fireEvent.click(screen.getByRole("button", { name: /create knowledge base/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/i);
    expect(screen.getByLabelText(/^name$/i)).toHaveValue("My references");
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();
  });

  it("coalesces duplicate submits and ignores a completion after unmount", async () => {
    let resolveCreate!: (value: KnowledgeCollection) => void;
    vi.mocked(createKnowledgeCollection).mockReturnValue(
      new Promise<KnowledgeCollection>((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const onSaved = vi.fn();
    const view = render(
      <KnowledgeCollectionForm onSaved={onSaved} scope="private" />,
    );
    setValue(/^name$/i, "My references");
    const form = screen.getByRole("button", { name: /create knowledge base/i }).closest("form");
    expect(form).not.toBeNull();

    fireEvent.submit(form!);
    fireEvent.submit(form!);
    expect(createKnowledgeCollection).toHaveBeenCalledTimes(1);
    view.unmount();
    await act(async () => {
      resolveCreate({ ...collection, is_active: true });
      await Promise.resolve();
    });
    expect(onSaved).not.toHaveBeenCalled();
  });

  it.each(["create", "update"] as const)(
    "ignores an old %s completion after rerendering into a new scope",
    async (operation) => {
      let resolveSave!: (value: KnowledgeCollection) => void;
      const pendingSave = new Promise<KnowledgeCollection>((resolve) => {
        resolveSave = resolve;
      });
      if (operation === "create") {
        vi.mocked(createKnowledgeCollection).mockReturnValue(pendingSave);
      } else {
        vi.mocked(updateKnowledgeCollection).mockReturnValue(pendingSave);
      }
      const onSaved = vi.fn();
      const onSavingChange = vi.fn();
      const editedCollection = operation === "update" ? collection : null;
      const view = render(
        <KnowledgeCollectionForm
          collection={editedCollection}
          onSaved={onSaved}
          onSavingChange={onSavingChange}
          scope="private"
        />,
      );
      setValue(/^name$/i, "Scope-bound references");

      fireEvent.click(
        screen.getByRole("button", {
          name: operation === "create" ? /create knowledge base/i : /^save$/i,
        }),
      );

      expect(
        operation === "create"
          ? createKnowledgeCollection
          : updateKnowledgeCollection,
      ).toHaveBeenCalledTimes(1);
      expect(onSavingChange).toHaveBeenLastCalledWith(true);

      view.rerender(
        <KnowledgeCollectionForm
          collection={editedCollection}
          onSaved={onSaved}
          onSavingChange={onSavingChange}
          scope="global"
        />,
      );
      await waitFor(() => expect(onSavingChange).toHaveBeenLastCalledWith(false));
      onSavingChange.mockClear();

      await act(async () => {
        resolveSave(collection);
        await Promise.resolve();
      });

      expect(onSaved).not.toHaveBeenCalled();
      expect(onSavingChange).not.toHaveBeenCalled();
    },
  );

  it("offers all Elasticsearch modes and resets hybrid to vector when switching to Qdrant", () => {
    render(<KnowledgeCollectionForm scope="private" />);
    const mode = screen.getByRole("combobox", { name: /retrieval mode/i });
    expect(screen.getByRole("option", { name: /keyword/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /hybrid/i })).toBeInTheDocument();

    fireEvent.change(mode, { target: { value: "hybrid" } });
    expect(screen.getByLabelText(/vector weight/i)).toHaveValue(0.7);
    fireEvent.change(screen.getByRole("combobox", { name: /^retriever$/i }), {
      target: { value: "qdrant" },
    });

    expect(mode).toHaveValue("vector");
    expect(screen.queryByRole("option", { name: /keyword/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /hybrid/i })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/supports vector retrieval only/i);
  });

  it("persists a user-selected Qdrant vector configuration", async () => {
    const config: KnowledgeCollectionConfig = {
      ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
      retriever_type: "qdrant",
    };
    vi.mocked(createKnowledgeCollection).mockResolvedValue({
      ...collection,
      config,
      is_active: true,
    });
    render(<KnowledgeCollectionForm scope="private" />);
    setValue(/^name$/i, "Qdrant manuals");
    fireEvent.change(screen.getByRole("combobox", { name: /^retriever$/i }), {
      target: { value: "qdrant" },
    });

    fireEvent.click(screen.getByRole("button", { name: /create knowledge base/i }));

    await waitFor(() =>
      expect(createKnowledgeCollection).toHaveBeenCalledWith("private", {
        name: "Qdrant manuals",
        config,
      }),
    );
  });

  it("persists Elasticsearch hybrid mode and custom linear weights", async () => {
    const config: KnowledgeCollectionConfig = {
      ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
      retrieval_mode: "hybrid",
      vector_weight: 0.6,
      keyword_weight: 0.4,
    };
    vi.mocked(createKnowledgeCollection).mockResolvedValue({
      ...collection,
      config,
      is_active: true,
    });
    render(<KnowledgeCollectionForm scope="private" />);
    setValue(/^name$/i, "Hybrid manuals");
    fireEvent.change(screen.getByRole("combobox", { name: /retrieval mode/i }), {
      target: { value: "hybrid" },
    });
    setValue(/vector weight/i, "0.6");
    setValue(/keyword weight/i, "0.4");

    fireEvent.click(screen.getByRole("button", { name: /create knowledge base/i }));

    await waitFor(() =>
      expect(createKnowledgeCollection).toHaveBeenCalledWith("private", {
        name: "Hybrid manuals",
        config,
      }),
    );
  });
});
