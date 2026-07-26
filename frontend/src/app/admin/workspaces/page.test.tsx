import { describe, expect, it, vi } from "vitest";

import GlobalWorkspacesPage from "./page";

const redirect = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ redirect }));

describe("global workspaces route", () => {
  it("redirects to the shared page that now owns public workspaces", () => {
    GlobalWorkspacesPage();

    expect(redirect).toHaveBeenCalledWith("/workspaces");
  });
});
