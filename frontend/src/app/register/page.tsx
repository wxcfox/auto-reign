"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { UserPlus } from "lucide-react";
import { type FormEvent, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { registerUser } from "@/lib/api";
import { setAuthToken } from "@/lib/auth";

const MIN_PASSWORD_LENGTH = 6;

export default function RegisterPage() {
  const router = useRouter();
  const { t } = useTranslation("common");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const trimmedUsername = username.trim();
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(t("auth.password_too_short"));
      return;
    }
    setPending(true);
    try {
      const response = await registerUser(trimmedUsername, password);
      setAuthToken(response.access_token);
      router.replace("/");
    } catch {
      setError(t("auth.register_failed"));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-page">
      <form className="auth-panel" onSubmit={submit}>
        <h1>{t("auth.register_title")}</h1>
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
            autoComplete="new-password"
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
          <UserPlus size={17} aria-hidden="true" />
          <span>{pending ? t("states.working") : t("auth.register_action")}</span>
        </button>
        <p className="auth-switch">
          <span>{t("auth.has_account")}</span>
          <Link href="/login">{t("auth.login_link")}</Link>
        </p>
      </form>
    </main>
  );
}
