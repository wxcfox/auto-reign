const TOKEN_KEY = "auto-reign-auth-token";
const tokenListeners = new Set<() => void>();

export function setAuthToken(token: string) {
  if (typeof window === "undefined") {
    return;
  }
  const previous = readStoredToken();
  try {
    window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Authentication should still fail closed when browser storage is unavailable.
  }
  if (readStoredToken() !== previous) notifyTokenListeners();
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const token = readStoredToken();
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
  const previous = readStoredToken();
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Clearing auth state is best-effort in restricted storage environments.
  }
  if (readStoredToken() !== previous) notifyTokenListeners();
}

export function isAuthenticated() {
  return Boolean(getAuthToken());
}

export function subscribeAuthToken(listener: () => void) {
  tokenListeners.add(listener);
  const handleStorage = (event: StorageEvent) => {
    if (event.storageArea === window.localStorage && event.key === TOKEN_KEY) {
      listener();
    }
  };
  if (typeof window !== "undefined") window.addEventListener("storage", handleStorage);
  return () => {
    tokenListeners.delete(listener);
    if (typeof window !== "undefined") window.removeEventListener("storage", handleStorage);
  };
}

function readStoredToken() {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function notifyTokenListeners() {
  for (const listener of [...tokenListeners]) listener();
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
