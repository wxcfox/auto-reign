import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeCollectionList } from "../KnowledgeCollectionList";
import { DEFAULT_KNOWLEDGE_COLLECTION_CONFIG } from "../KnowledgeCollectionForm";
import i18next from "@/i18n/setup";
import {
  createKnowledgeCollection,
  deleteKnowledgeCollection,
  getCurrentUser,
  listKnowledgeCollections,
  updateKnowledgeCollection,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { KnowledgeCollection, User } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  createKnowledgeCollection: vi.fn(),
  deleteKnowledgeCollection: vi.fn(),
  getCurrentUser: vi.fn(),
  listKnowledgeCollections: vi.fn(),
  updateKnowledgeCollection: vi.fn(),
}));

vi.mock("@/components/KnowledgeDocumentTable", () => ({
  KnowledgeDocumentTable: (props: { collectionId: string; canManage?: boolean }) => (
    <div data-can-manage={String(props.canManage === true)} data-testid="document-table">
      documents:{props.collectionId}
    </div>
  ),
}));

vi.mock("@/components/KnowledgeUploader", () => ({
  KnowledgeUploader: (props: { collectionId: string }) => (
    <div data-testid="uploader">uploader:{props.collectionId}</div>
  ),
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

// As an ordinary account sees it: visible and usable, but not editable. The
// backend flips can_manage to true for an administrator.
const globalCollection: KnowledgeCollection = {
  ...privateCollection,
  id: "global-collection",
  name: "Global handbook",
  scope: "global",
  can_manage: false,
  is_active: true,
};

const adminManagedGlobalCollection: KnowledgeCollection = {
  ...globalCollection,
  can_manage: true,
};

function mockUser(role: User["role"] = "user") {
  vi.mocked(getCurrentUser).mockResolvedValue({
    id: 7,
    username: "alice",
    role,
  } as User);
}

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
    mockUser();
    await i18next.changeLanguage("en");
  });

  it("loads owned inactive and active global collections in parallel, dedupes, and keeps shared definitions read-only", async () => {
    mockPrivateLists([privateCollection, globalCollection], [globalCollection]);
    render(<KnowledgeCollectionList />);

    await waitFor(() => {
      expect(listKnowledgeCollections).toHaveBeenCalledWith("owned", {
        includeInactive: true,
      });
      expect(listKnowledgeCollections).toHaveBeenCalledWith("global");
    });
    await screen.findByRole("heading", { name: privateCollection.name });
    expect(screen.queryByText(globalCollection.name)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /edit my manuals/i })).toBeEnabled();

    fireEvent.click(screen.getByRole("tab", { name: /public knowledge/i }));
    expect(await screen.findByRole("heading", { name: globalCollection.name }))
      .toBeInTheDocument();
    expect(screen.queryByText(privateCollection.name)).not.toBeInTheDocument();
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
    render(<KnowledgeCollectionList />);
    await screen.findByRole("heading", { name: privateCollection.name });

    fireEvent.click(screen.getByRole("button", { name: /^create knowledge base$/i }));
    const editor = screen.getByRole("region", { name: /^create knowledge base$/i });
    expect(screen.getByRole("button", { name: /edit my manuals/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /enable my manuals/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /delete my manuals/i })).toBeDisabled();
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

  it("uses global mutation authority for a public collection on the Public tab", async () => {
    mockUser("admin");
    mockPrivateLists([], [adminManagedGlobalCollection]);
    vi.mocked(updateKnowledgeCollection).mockResolvedValue({
      ...adminManagedGlobalCollection,
      config: { ...adminManagedGlobalCollection.config, top_k: 10 },
    });
    render(<KnowledgeCollectionList />);

    fireEvent.click(await screen.findByRole("tab", { name: /public/i }));
    await screen.findByRole("heading", { name: globalCollection.name });
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
    render(<KnowledgeCollectionList />);
    await screen.findByRole("heading", { name: privateCollection.name });

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
    render(<KnowledgeCollectionList />);
    await screen.findByRole("heading", { name: privateCollection.name });

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
    render(<KnowledgeCollectionList />);
    await screen.findByRole("heading", { name: privateCollection.name });

    fireEvent.click(screen.getByRole("button", { name: /delete my manuals/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /active Agent reference.*active document.*unfinished document cleanup/i,
    );
    expect(screen.getByRole("heading", { name: privateCollection.name })).toBeInTheDocument();
    expect(screen.queryByText(/document ids must stay private/i)).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("hides creation from a non-admin standing on the Public tab", async () => {
    mockPrivateLists([privateCollection], [globalCollection]);
    render(<KnowledgeCollectionList />);
    await screen.findByRole("heading", { name: privateCollection.name });

    expect(
      screen.getByRole("button", { name: /create knowledge base/i }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /public/i }));

    await screen.findByRole("heading", { name: globalCollection.name });
    expect(screen.queryByRole("button", { name: /create/i })).not.toBeInTheDocument();
  });

  it("publishes through the admin endpoint when an admin creates on the Public tab", async () => {
    mockUser("admin");
    mockPrivateLists([privateCollection], [globalCollection]);
    vi.mocked(createKnowledgeCollection).mockResolvedValue({
      ...globalCollection,
      id: "new-global",
      name: "Team handbook",
    });
    render(<KnowledgeCollectionList />);
    await screen.findByRole("heading", { name: privateCollection.name });

    fireEvent.click(screen.getByRole("tab", { name: /public/i }));
    fireEvent.click(
      await screen.findByRole("button", { name: /create public knowledge base/i }),
    );
    const editor = screen.getByRole("region", { name: /create public knowledge base/i });
    fireEvent.change(within(editor).getByLabelText(/^name$/i), {
      target: { value: "Team handbook" },
    });
    // The form's submit keeps the neutral label; the tab carries the scope.
    fireEvent.click(
      within(editor).getByRole("button", { name: /^create knowledge base$/i }),
    );

    // The tab decides the scope; creating here must reach every user, so it has
    // to go to the admin base rather than silently minting a private collection.
    await waitFor(() =>
      expect(createKnowledgeCollection).toHaveBeenCalledWith("global", {
        name: "Team handbook",
        config: { ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG },
      }),
    );
  });

  it("creates a private collection when an admin stays on the Personal tab", async () => {
    mockUser("admin");
    mockPrivateLists([privateCollection], [globalCollection]);
    vi.mocked(createKnowledgeCollection).mockResolvedValue({
      ...privateCollection,
      id: "new-private",
      name: "Personal notes",
    });
    render(<KnowledgeCollectionList />);
    await screen.findByRole("heading", { name: privateCollection.name });

    fireEvent.click(screen.getByRole("button", { name: /^create knowledge base$/i }));
    const editor = screen.getByRole("region", { name: /^create knowledge base$/i });
    fireEvent.change(within(editor).getByLabelText(/^name$/i), {
      target: { value: "Personal notes" },
    });
    fireEvent.click(
      within(editor).getByRole("button", { name: /^create knowledge base$/i }),
    );

    await waitFor(() =>
      expect(createKnowledgeCollection).toHaveBeenCalledWith("private", {
        name: "Personal notes",
        config: { ...DEFAULT_KNOWLEDGE_COLLECTION_CONFIG },
      }),
    );
  });


  it("renders a recoverable stable error in Chinese", async () => {
    await i18next.changeLanguage("zh-CN");
    vi.mocked(listKnowledgeCollections)
      .mockRejectedValueOnce(new Error("database secret"))
      .mockResolvedValue({ collections: [] });
    render(<KnowledgeCollectionList />);

    expect(await screen.findByRole("alert")).toHaveTextContent("资料库加载失败。");
    expect(screen.queryByText(/database secret/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无资料库。")).toBeInTheDocument();
  });

  it("selects a collection from the sidebar and shows its documents on the right", async () => {
    mockPrivateLists([privateCollection], [globalCollection]);
    render(<KnowledgeCollectionList />);
    await screen.findByRole("heading", { name: privateCollection.name });

    expect(screen.getByTestId("document-table")).toHaveTextContent(privateCollection.id);

    fireEvent.click(screen.getByRole("tab", { name: /public knowledge/i }));
    await screen.findByRole("heading", { name: globalCollection.name });
    expect(screen.getByTestId("document-table")).toHaveTextContent(globalCollection.id);
    expect(screen.getByTestId("document-table")).toHaveAttribute("data-can-manage", "false");
    expect(screen.queryByTestId("uploader")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /personal knowledge/i }));
    await screen.findByRole("heading", { name: privateCollection.name });
    expect(screen.getByTestId("document-table")).toHaveTextContent(privateCollection.id);
    expect(screen.getByTestId("document-table")).toHaveAttribute("data-can-manage", "true");
    expect(screen.getByTestId("uploader")).toBeInTheDocument();
  });

  it("shows a distinct message when a search query filters out every collection", async () => {
    mockPrivateLists([privateCollection], [globalCollection]);
    render(<KnowledgeCollectionList />);
    await screen.findByRole("heading", { name: privateCollection.name });

    fireEvent.change(screen.getByLabelText(/search knowledge bases/i), {
      target: { value: "no such collection" },
    });

    expect(await screen.findByText("No matching knowledge bases found.")).toBeInTheDocument();
    expect(screen.queryByText("No knowledge bases yet.")).not.toBeInTheDocument();
  });
});
