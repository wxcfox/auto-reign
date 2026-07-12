import { describe, expect, it, vi } from "vitest";

import RootPage from "./page";
import { metadata } from "./layout";

const navigationMocks = vi.hoisted(() => ({ redirect: vi.fn() }));

vi.mock("next/navigation", () => ({
  redirect: navigationMocks.redirect,
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn() }),
}));

describe("root route", () => {
  it("redirects to unified chat on the server", () => {
    RootPage();

    expect(navigationMocks.redirect).toHaveBeenCalledWith("/chat");
  });

  it("describes the generic Agent chat platform", () => {
    expect(metadata.description).toBe("Local-first Agent chat platform");
  });
});
