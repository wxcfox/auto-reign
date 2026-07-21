import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeCollectionList } from "../KnowledgeCollectionList";
import { DEFAULT_KNOWLEDGE_COLLECTION_CONFIG } from "../KnowledgeCollectionForm";
import i18next from "@/i18n/setup";
import {
  createKnowledgeCollection,
  deleteKnowledgeCollection,
  listKnowledgeCollections,
  updateKnowledgeCollection,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { KnowledgeCollection } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  createKnowledgeCollection: vi.fn(),
  deleteKnowledgeCollection: vi.fn(),
  listKnowledgeCollections: vi.fn(),
  updateKnowledgeCollection: vi.fn(),
}));

const privateCollection: KnowledgeCollection = {
  id: "private-collection",
  name: "My manuals",
  scope: "private",
  can_manage: true,
  config: { ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG },
  is_active: false,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const globalCollection: KnowledgeCollection = {
  ...privateCollection,
  id: "global-collection",
  name: "Global handbook",
  scope: "global",
  can_manage: true,
  is_active: true,
};

function mockPrivateLists(
  owned: KnowledgeCollection[] = [privateCollection],
  shared: KnowledgeCollection[] = [globalCollection],
) {
  vi.mocked(listKnowledgeCollections).mockImplementation(async (scope) => ({
    collections: scope === "owned" ? owned : shared,
  }));
}

describe("KnowledgeCollectionList management page", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18next.changeLanguage("en");
  });

  it("loads owned inactive and active global collections in parallel, dedupes, and keeps shared definitions read-only", async () => {
    mockPrivateLists([privateCollection, globalCollection], [globalCollection]);
    render(<KnowledgeCollectionList scope="private" />);

    await waitFor(() => {
      expect(listKnowledgeCollections).toHaveBeenCalledWith("owned", {
        includeInactive: true,
      });
      expect(listKnowledgeCollections).toHaveBeenCalledWith("global");
    });
    expect(screen.getAllByText(globalCollection.name)).toHaveLength(1);
    expect(screen.queryByRole("link", { name: /open documents in my manuals/i }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open documents in global handbook/i }))
      .toHaveAttribute("href", "/knowledge/global-collection");
    expect(screen.getByRole("button", { name: /edit my manuals/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /edit global handbook/i }))
      .not.toBeInTheDocument();
  });

  it("creates only a private collection and locks competing actions until the editor closes", async () => {
    mockPrivateLists();
    vi.mocked(createKnowledgeCollection).mockResolvedValue({
      ...privateCollection,
      id: "new-private",
      name: "Private references",
      is_active: true,
    });
    render(<KnowledgeCollectionList scope="private" />);
    await screen.findByText(privateCollection.name);

    fireEvent.click(screen.getByRole("button", { name: /^create knowledge base$/i }));
    const editor = screen.getByRole("region", { name: /^create knowledge base$/i });
    expect(screen.getByRole("button", { name: /edit my manuals/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /enable my manuals/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /delete my manuals/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /open documents in global handbook/i }))
      .toBeDisabled();
    expect(screen.queryByRole("link", { name: /open documents in global handbook/i }))
      .not.toBeInTheDocument();
    fireEvent.change(within(editor).getByLabelText(/^name$/i), {
      target: { value: "Private references" },
    });
    fireEvent.click(
      within(editor).getByRole("button", { name: /create knowledge base/i }),
    );

    await waitFor(() =>
      expect(createKnowledgeCollection).toHaveBeenCalledWith("private", {
        name: "Private references",
        config: DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: /^create knowledge base$/i }),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: /^create knowledge base$/i }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: /edit my manuals/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /enable my manuals/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /delete my manuals/i })).toBeEnabled();
    expect(screen.queryByRole("combobox", { name: /visibility|owner|scope/i }))
      .not.toBeInTheDocument();
  });

  it("uses global list and mutation authority while keeping the document entry on the unified route", async () => {
    vi.mocked(listKnowledgeCollections).mockResolvedValue({
      collections: [globalCollection],
    });
    vi.mocked(updateKnowledgeCollection).mockResolvedValue({
      ...globalCollection,
      config: { ...globalCollection.config, top_k: 10 },
    });
    render(<KnowledgeCollectionList scope="global" />);

    await waitFor(() =>
      expect(listKnowledgeCollections).toHaveBeenCalledWith("global", {
        includeInactive: true,
      }),
    );
    expect(screen.getByRole("link", { name: /open documents in global handbook/i }))
      .toHaveAttribute("href", "/knowledge/global-collection");
    fireEvent.click(screen.getByRole("button", { name: /edit global handbook/i }));
    const editor = screen.getByRole("region", { name: /edit global handbook/i });
    fireEvent.change(within(editor).getByLabelText(/top k/i), {
      target: { value: "10" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(updateKnowledgeCollection).toHaveBeenCalledWith(
        "global",
        globalCollection.id,
        {
          name: globalCollection.name,
          config: { ...globalCollection.config, top_k: 10 },
          is_active: true,
        },
      ),
    );
  });

  it("reactivates an inactive collection once and sends its full config", async () => {
    let resolveUpdate!: (value: KnowledgeCollection) => void;
    vi.mocked(listKnowledgeCollections).mockResolvedValue({
      collections: [privateCollection],
    });
    vi.mocked(updateKnowledgeCollection).mockReturnValue(
      new Promise<KnowledgeCollection>((resolve) => {
        resolveUpdate = resolve;
      }),
    );
    render(<KnowledgeCollectionList scope="private" />);
    await screen.findByText(privateCollection.name);

    const enable = screen.getByRole("button", { name: /enable my manuals/i });
    fireEvent.click(enable);
    fireEvent.click(enable);
    expect(updateKnowledgeCollection).toHaveBeenCalledTimes(1);
    expect(updateKnowledgeCollection).toHaveBeenCalledWith(
      "private",
      privateCollection.id,
      {
        name: privateCollection.name,
        config: privateCollection.config,
        is_active: true,
      },
    );
    await act(async () => {
      resolveUpdate({ ...privateCollection, is_active: true });
      await Promise.resolve();
    });
  });

  it("honors can_manage even for an owned-looking response", async () => {
    mockPrivateLists([{ ...privateCollection, can_manage: false }], []);
    render(<KnowledgeCollectionList scope="private" />);
    await screen.findByText(privateCollection.name);

    expect(screen.queryByRole("button", { name: /edit my manuals/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /enable my manuals/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete my manuals/i }))
      .not.toBeInTheDocument();
  });

  it("retains the row and reports all resource_in_use collection causes", async () => {
    mockPrivateLists();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(deleteKnowledgeCollection).mockRejectedValue(
      new ApiError("document ids must stay private", {
        code: "resource_in_use",
        status: 409,
      }),
    );
    render(<KnowledgeCollectionList scope="private" />);
    await screen.findByText(privateCollection.name);

    fireEvent.click(screen.getByRole("button", { name: /delete my manuals/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /active Agent reference.*active document.*unfinished document cleanup/i,
    );
    expect(screen.getByText(privateCollection.name)).toBeInTheDocument();
    expect(screen.queryByText(/document ids must stay private/i)).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("ignores stale private results after switching to global management", async () => {
    let resolveOwned!: (value: { collections: KnowledgeCollection[] }) => void;
    let resolveShared!: (value: { collections: KnowledgeCollection[] }) => void;
    vi.mocked(listKnowledgeCollections).mockImplementation((listScope, options) => {
      if (listScope === "owned") {
        return new Promise((resolve) => {
          resolveOwned = resolve;
        });
      }
      if (!options?.includeInactive) {
        return new Promise((resolve) => {
          resolveShared = resolve;
        });
      }
      return Promise.resolve({ collections: [globalCollection] });
    });
    const view = render(<KnowledgeCollectionList scope="private" />);

    view.rerender(<KnowledgeCollectionList scope="global" />);
    expect(await screen.findByText(globalCollection.name)).toBeInTheDocument();
    await act(async () => {
      resolveOwned({ collections: [privateCollection] });
      resolveShared({ collections: [] });
      await Promise.resolve();
    });
    expect(screen.queryByText(privateCollection.name)).not.toBeInTheDocument();
  });

  it("hides a ready private scope immediately while the next global scope is pending", async () => {
    let resolveGlobal!: (value: { collections: KnowledgeCollection[] }) => void;
    vi.mocked(listKnowledgeCollections).mockImplementation((listScope, options) => {
      if (listScope === "owned") {
        return Promise.resolve({ collections: [privateCollection] });
      }
      if (!options?.includeInactive) {
        return Promise.resolve({ collections: [globalCollection] });
      }
      return new Promise((resolve) => {
        resolveGlobal = resolve;
      });
    });
    const view = render(<KnowledgeCollectionList scope="private" />);
    await screen.findByText(privateCollection.name);
    expect(screen.getByText(globalCollection.name)).toBeInTheDocument();

    view.rerender(<KnowledgeCollectionList scope="global" />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
    expect(screen.queryByText(privateCollection.name)).not.toBeInTheDocument();
    expect(screen.queryByText(globalCollection.name)).not.toBeInTheDocument();

    await act(async () => {
      resolveGlobal({ collections: [globalCollection] });
      await Promise.resolve();
    });
    expect(await screen.findByText(globalCollection.name)).toBeInTheDocument();
  });

  it("renders a recoverable stable error in Chinese", async () => {
    await i18next.changeLanguage("zh-CN");
    vi.mocked(listKnowledgeCollections)
      .mockRejectedValueOnce(new Error("database secret"))
      .mockResolvedValue({ collections: [] });
    render(<KnowledgeCollectionList scope="global" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("资料库加载失败。");
    expect(screen.queryByText(/database secret/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无资料库。")).toBeInTheDocument();
  });
});
