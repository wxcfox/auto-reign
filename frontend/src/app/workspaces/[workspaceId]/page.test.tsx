import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WorkspacePage from "./page";
import i18next from "@/i18n/setup";

vi.mock("@/components/WorkspaceBrowser", () => ({
  WorkspaceBrowser: ({ scope, workspaceId }: { scope: string; workspaceId: string }) => (
    <div data-scope={scope} data-workspace-id={workspaceId} data-testid="workspace-browser" />
  ),
}));

describe("workspace detail page", () => {
  beforeEach(async () => {
    await i18next.changeLanguage("en");
  });

  it("renders the selected workspace through the current-user file authority", async () => {
    render(<WorkspacePage params={Promise.resolve({ workspaceId: "ws-1" })} />);

    expect(
      await screen.findByRole("heading", { name: /Agent Home/i }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("workspace-browser")).toHaveAttribute(
      "data-workspace-id",
      "ws-1",
    );
    expect(screen.getByTestId("workspace-browser")).toHaveAttribute(
      "data-scope",
      "private",
    );
    expect(screen.getByRole("link", { name: /back to workspaces/i })).toHaveAttribute(
      "href",
      "/workspaces",
    );
  });

  it("localizes the Agent Home page without changing its authority scope", async () => {
    await i18next.changeLanguage("zh-CN");
    render(<WorkspacePage params={Promise.resolve({ workspaceId: "global-ws" })} />);

    expect(await screen.findByRole("heading", { name: "智能体文件中台" })).toBeInTheDocument();
    expect(screen.getByTestId("workspace-browser")).toHaveAttribute(
      "data-scope",
      "private",
    );
  });
});
