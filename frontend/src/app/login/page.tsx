"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { LogIn } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { getCurrentUser, loginUser } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { setAuthToken } from "@/lib/auth";

export default function LoginPage() {
  const { replace } = useRouter();
  const searchParams = useSearchParams();
  const { t } = useTranslation("common");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const mountedRef = useRef(true);
  const submittingRef = useRef(false);
  const loginSucceededRef = useRef(false);
  const deferredSetupRequiredRef = useRef(false);
  const setupProbeRef = useRef<ReturnType<typeof getCurrentUser> | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    let cancelled = false;
    setupProbeRef.current ??= probeInitialAdminSetup(
      () => mountedRef.current && !loginSucceededRef.current,
    );
    void setupProbeRef.current.catch((probeError: unknown) => {
      if (
        cancelled ||
        !mountedRef.current ||
        loginSucceededRef.current ||
        !isAdminSetupRequired(probeError)
      ) {
        return;
      }
      if (submittingRef.current) {
        deferredSetupRequiredRef.current = true;
        return;
      }
      replace("/setup");
    });
    return () => {
      cancelled = true;
      mountedRef.current = false;
    };
  }, [replace]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) {
      return;
    }
    submittingRef.current = true;
    loginSucceededRef.current = false;
    setError(null);
    setPending(true);
    try {
      const response = await loginUser(username.trim(), password);
      if (!mountedRef.current) {
        return;
      }
      loginSucceededRef.current = true;
      deferredSetupRequiredRef.current = false;
      setAuthToken(response.access_token);
      replace(safeRedirect(searchParams.get("redirect")));
    } catch {
      if (!mountedRef.current) {
        return;
      }
      if (deferredSetupRequiredRef.current) {
        deferredSetupRequiredRef.current = false;
        replace("/setup");
      } else {
        setError(t("auth.invalid_credentials"));
      }
    } finally {
      submittingRef.current = false;
      if (mountedRef.current) {
        setPending(false);
      }
    }
  }

  return (
    <main className="auth-page">
      <form className="auth-panel" onSubmit={submit}>
        <h1>{t("auth.login_title")}</h1>
        <label className="auth-field">
          <span>{t("auth.username")}</span>
          <input
            autoComplete="username"
            onChange={(event) => setUsername(event.target.value)}
            value={username}
          />
        </label>
        <label className="auth-field">
          <span>{t("auth.password")}</span>
          <input
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
        </label>
        {error ? (
          <p role="alert" className="form-error">
            {error}
          </p>
        ) : null}
        <button
          className="button button-primary"
          disabled={pending || !username.trim() || !password}
          type="submit"
        >
          <LogIn size={17} aria-hidden="true" />
          <span>{pending ? t("states.working") : t("auth.login_action")}</span>
        </button>
      </form>
    </main>
  );
}

function safeRedirect(value: string | null) {
  if (!value) {
    return "/";
  }
  const pathEnd = value.search(/[?#]/);
  const rawPathname = pathEnd < 0 ? value : value.slice(0, pathEnd);
  if (
    !rawPathname.startsWith("/") ||
    rawPathname.startsWith("//") ||
    rawPathname.includes("\\")
  ) {
    return "/";
  }
  let parsed: URL;
  try {
    parsed = new URL(value, "http://auto-reign.local");
  } catch {
    return "/";
  }
  if (parsed.origin !== "http://auto-reign.local") {
    return "/";
  }
  const decodedPathname = fullyDecodePath(parsed.pathname);
  const authComparablePath = decodedPathname?.replace(/\/+$/, "") || "/";
  if (
    decodedPathname === null ||
    authComparablePath === "/login" ||
    authComparablePath === "/setup"
  ) {
    return "/";
  }
  return `${parsed.pathname}${parsed.search}${parsed.hash}`;
}

function isAdminSetupRequired(error: unknown) {
  return (
    error instanceof ApiError &&
    error.status === 400 &&
    error.code === "admin_password_setup_required"
  );
}

async function probeInitialAdminSetup(shouldRetry: () => boolean) {
  try {
    return await getCurrentUser();
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401 || !shouldRetry()) {
      throw error;
    }
    return getCurrentUser();
  }
}

function fullyDecodePath(value: string): string | null {
  let candidate = value;
  for (let depth = 0; depth < 8; depth += 1) {
    if (!candidate.startsWith("/") || candidate.startsWith("//") || candidate.includes("\\")) {
      return null;
    }
    let decoded: string;
    try {
      decoded = decodeURIComponent(candidate);
    } catch {
      return null;
    }
    if (decoded === candidate) {
      return decoded;
    }
    candidate = decoded;
  }
  return null;
}
