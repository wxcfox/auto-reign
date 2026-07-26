import { describe, expect, it, vi } from "vitest";

import GlobalKnowledgePage from "./page";

const redirect = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ redirect }));

describe("global knowledge route", () => {
  it("redirects to the shared page that now owns public knowledge bases", () => {
    GlobalKnowledgePage();

    expect(redirect).toHaveBeenCalledWith("/knowledge");
  });
});
