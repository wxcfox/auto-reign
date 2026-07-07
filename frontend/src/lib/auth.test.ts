import { beforeEach, describe, expect, it } from "vitest";

import { clearAuthToken, getAuthToken, setAuthToken } from "./auth";

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
});
