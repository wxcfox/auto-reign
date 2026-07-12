import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import GlobalKnowledgePage from "./page";
import { KnowledgeCollectionList } from "@/components/KnowledgeCollectionList";
import { RoleGuard } from "@/components/RoleGuard";

vi.mock("@/components/RoleGuard", () => ({
  RoleGuard: vi.fn(({ children, role }: { children: ReactNode; role: string }) => (
    <div data-role={role}>{children}</div>
  )),
}));

vi.mock("@/components/KnowledgeCollectionList", () => ({
  KnowledgeCollectionList: vi.fn(({ scope }: { scope: string }) => (
    <div data-scope={scope}>Global Knowledge management</div>
  )),
}));

describe("global knowledge route", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("guards global Knowledge management and passes only global scope", () => {
    render(<GlobalKnowledgePage />);

    expect(screen.getByText("Global Knowledge management")).toHaveAttribute(
      "data-scope",
      "global",
    );
    expect(RoleGuard).toHaveBeenCalledWith(
      expect.objectContaining({ role: "admin" }),
      undefined,
    );
    expect(KnowledgeCollectionList).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "global" }),
      undefined,
    );
  });
});
