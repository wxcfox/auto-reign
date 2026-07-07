"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { LogIn } from "lucide-react";
import { type FormEvent, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { loginUser } from "@/lib/api";
import { setAuthToken } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { t } = useTranslation("common");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      const response = await loginUser(username.trim(), password);
      setAuthToken(response.access_token);
      router.replace(safeRedirect(searchParams.get("redirect")));
    } catch {
      setError(t("auth.invalid_credentials"));
    } finally {
      setPending(false);
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
        <p className="auth-switch">
          <span>{t("auth.no_account")}</span>
          <Link href="/register">{t("auth.create_account_link")}</Link>
        </p>
      </form>
    </main>
  );
}

function safeRedirect(value: string | null) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/";
  }
  if (value === "/login" || value === "/register") {
    return "/";
  }
  return value;
}
