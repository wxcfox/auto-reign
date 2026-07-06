import type { User } from "./types";

const TOKEN_KEY = "auto-reign-auth-token";

export type AuthUser = User;

export function setAuthToken(token: string) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Authentication should still fail closed when browser storage is unavailable.
  }
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  let token: string | null = null;
  try {
    token = window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
  if (!token || isTokenExpired(token)) {
    clearAuthToken();
    return null;
  }
  return token;
}

export function clearAuthToken() {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Clearing auth state is best-effort in restricted storage environments.
  }
}

export function isAuthenticated() {
  return Boolean(getAuthToken());
}

function isTokenExpired(token: string) {
  try {
    const payload = JSON.parse(decodeBase64Url(token.split(".")[1])) as { exp?: unknown };
    if (typeof payload.exp !== "number") {
      return false;
    }
    return Date.now() >= payload.exp * 1000;
  } catch {
    return false;
  }
}

function decodeBase64Url(value: string | undefined) {
  if (!value) {
    return "";
  }
  const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
  const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
  return window.atob(padded);
}
