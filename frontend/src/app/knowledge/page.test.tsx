import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import KnowledgePage from "./page";
import { KnowledgeCollectionList } from "@/components/KnowledgeCollectionList";

vi.mock("@/components/KnowledgeCollectionList", () => ({
  KnowledgeCollectionList: vi.fn(() => <div>Knowledge management</div>),
}));

describe("knowledge route", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders one Knowledge list for every role, with the tab carrying the scope", () => {
    render(<KnowledgePage />);

    expect(screen.getByText("Knowledge management")).toBeInTheDocument();
    expect(KnowledgeCollectionList).toHaveBeenCalledWith({}, undefined);
  });
});
