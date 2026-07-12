import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import KnowledgePage from "./page";
import { KnowledgeCollectionList } from "@/components/KnowledgeCollectionList";

vi.mock("@/components/KnowledgeCollectionList", () => ({
  KnowledgeCollectionList: vi.fn(({ scope }: { scope: string }) => (
    <div data-scope={scope}>Knowledge management</div>
  )),
}));

describe("personal knowledge route", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders private Knowledge management without an admin guard", () => {
    render(<KnowledgePage />);

    expect(screen.getByText("Knowledge management")).toHaveAttribute(
      "data-scope",
      "private",
    );
    expect(KnowledgeCollectionList).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "private" }),
      undefined,
    );
  });
});
