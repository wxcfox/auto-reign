import { describe, expect, it, vi } from "vitest";

import GlobalAgentsPage from "./page";

const redirect = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ redirect }));

describe("global agents route", () => {
  it("redirects to the shared page that now owns public agents", () => {
    GlobalAgentsPage();

    expect(redirect).toHaveBeenCalledWith("/agents");
  });
});
