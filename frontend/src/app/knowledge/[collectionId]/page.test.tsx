import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import KnowledgeCollectionPage from "./page";
import { KnowledgeDocumentTable } from "@/components/KnowledgeDocumentTable";
import { KnowledgeUploader } from "@/components/KnowledgeUploader";
import i18next from "@/i18n/setup";
import { getKnowledgeCollection } from "@/lib/api";
import type { KnowledgeCollection } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getKnowledgeCollection: vi.fn(),
}));

vi.mock("@/components/KnowledgeUploader", () => ({
  KnowledgeUploader: vi.fn(() => <div>Document uploader</div>),
}));

vi.mock("@/components/KnowledgeDocumentTable", () => ({
  KnowledgeDocumentTable: vi.fn(({ canManage }: { canManage: boolean }) => (
    <div data-can-manage={String(canManage)}>Document table</div>
  )),
}));

const globalCollection: KnowledgeCollection = {
  id: "global-collection",
  name: "Global handbook",
  scope: "global",
  can_manage: true,
  config: { chunk_size: 900, chunk_overlap: 120, top_k: 8, score_threshold: null },
  is_active: true,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

describe("knowledge collection detail authority", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18next.changeLanguage("en");
  });

  it("lets an administrator manage documents in a global collection on the unified detail route", async () => {
    vi.mocked(getKnowledgeCollection).mockResolvedValue(globalCollection);
    render(
      <KnowledgeCollectionPage
        params={Promise.resolve({ collectionId: globalCollection.id })}
      />,
    );

    expect(await screen.findByText("Document uploader")).toBeInTheDocument();
    expect(screen.getByText("Document table")).toHaveAttribute(
      "data-can-manage",
      "true",
    );
    expect(KnowledgeUploader).toHaveBeenCalledWith(
      expect.objectContaining({ collectionId: globalCollection.id }),
      undefined,
    );
    expect(KnowledgeDocumentTable).toHaveBeenCalledWith(
      expect.objectContaining({
        canManage: true,
        collectionId: globalCollection.id,
      }),
      undefined,
    );
  });

  it("keeps the same global documents read-only for an ordinary user", async () => {
    vi.mocked(getKnowledgeCollection).mockResolvedValue({
      ...globalCollection,
      can_manage: false,
    });
    render(
      <KnowledgeCollectionPage
        params={Promise.resolve({ collectionId: globalCollection.id })}
      />,
    );

    expect(await screen.findByText("Document table")).toHaveAttribute(
      "data-can-manage",
      "false",
    );
    expect(screen.queryByText("Document uploader")).not.toBeInTheDocument();
    expect(KnowledgeUploader).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(KnowledgeDocumentTable).toHaveBeenCalledWith(
        expect.objectContaining({ canManage: false }),
        undefined,
      ),
    );
  });
});
