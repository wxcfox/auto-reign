import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearAuthToken, getAuthToken, setAuthToken, subscribeAuthToken } from "./auth";

function tokenWithExp(exp: number) {
  const payload = window
    .btoa(JSON.stringify({ exp }))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
  return `header.${payload}.signature`;
}

describe("auth token storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("stores and reads token", () => {
    setAuthToken("token-1");

    expect(getAuthToken()).toBe("token-1");
  });

  it("clears token", () => {
    setAuthToken("token-1");
    clearAuthToken();

    expect(getAuthToken()).toBeNull();
  });

  it("clears expired JWT tokens", () => {
    setAuthToken(tokenWithExp(Math.floor(Date.now() / 1000) - 10));

    expect(getAuthToken()).toBeNull();
    expect(localStorage.getItem("auto-reign-auth-token")).toBeNull();
  });

  it("notifies same-page subscribers only when the stored identity changes", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeAuthToken(listener);

    setAuthToken("token-1");
    setAuthToken("token-1");
    setAuthToken("token-2");
    clearAuthToken();
    clearAuthToken();

    expect(listener).toHaveBeenCalledTimes(3);
    unsubscribe();
    setAuthToken("token-3");
    expect(listener).toHaveBeenCalledTimes(3);
  });
});
