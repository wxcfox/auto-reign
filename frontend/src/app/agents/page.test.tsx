import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentsPage from "./page";
import { AgentManagementPage } from "@/components/AgentManagementPage";

vi.mock("@/components/AgentManagementPage", () => ({
  AgentManagementPage: vi.fn(({ initialCreate, scope }: { initialCreate: boolean; scope: string }) => (
    <div data-scope={scope}>
      Agent management
      {initialCreate ? <div aria-label="Create Agent" role="dialog" /> : null}
    </div>
  )),
}));

describe("personal agents route", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders private Agent management without an admin guard", async () => {
    const page = await AgentsPage({ searchParams: Promise.resolve({}) });
    render(page);

    expect(screen.getByText("Agent management")).toHaveAttribute("data-scope", "private");
    expect(AgentManagementPage).toHaveBeenCalledWith(
      expect.objectContaining({ initialCreate: false, scope: "private" }),
      undefined,
    );
  });

  it("opens the private create form only for create=1", async () => {
    const page = await AgentsPage({
      searchParams: Promise.resolve({ create: "1" }),
    });
    render(page);

    expect(screen.getByRole("dialog", { name: "Create Agent" })).toBeInTheDocument();
    expect(AgentManagementPage).toHaveBeenCalledWith(
      expect.objectContaining({ initialCreate: true, scope: "private" }),
      undefined,
    );
  });
});
