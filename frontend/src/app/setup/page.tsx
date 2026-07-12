"use client";

import { ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { setupAdminPassword } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { setAuthToken } from "@/lib/auth";
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from "@/lib/limits";

export default function SetupPage() {
  const router = useRouter();
  const { t } = useTranslation("common");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const submittingRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submittingRef.current) {
      return;
    }
    setError(null);
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(t("auth.password_too_short"));
      return;
    }
    if (password.length > MAX_PASSWORD_LENGTH) {
      setError(t("auth.password_too_long"));
      return;
    }
    if (password !== confirmation) {
      setError(t("auth.password_mismatch"));
      return;
    }

    submittingRef.current = true;
    setPending(true);
    try {
      const response = await setupAdminPassword(password);
      if (!mountedRef.current) {
        return;
      }
      setAuthToken(response.access_token);
      router.replace("/chat");
    } catch (submitError) {
      if (!mountedRef.current) {
        return;
      }
      if (
        submitError instanceof ApiError &&
        submitError.status === 409 &&
        submitError.code === "admin_password_already_initialized"
      ) {
        router.replace("/login");
        return;
      }
      setError(t("auth.setup_failed"));
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
        <h1>{t("auth.setup_title")}</h1>
        <p>{t("auth.setup_description")}</p>
        <label className="auth-field">
          <span>{t("auth.new_password")}</span>
          <input
            autoComplete="new-password"
            disabled={pending}
            maxLength={MAX_PASSWORD_LENGTH}
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
        </label>
        <label className="auth-field">
          <span>{t("auth.confirm_password")}</span>
          <input
            autoComplete="new-password"
            disabled={pending}
            maxLength={MAX_PASSWORD_LENGTH}
            onChange={(event) => setConfirmation(event.target.value)}
            type="password"
            value={confirmation}
          />
        </label>
        {error ? (
          <p role="alert" className="form-error">
            {error}
          </p>
        ) : null}
        <button
          className="button button-primary"
          disabled={pending || !password || !confirmation}
          type="submit"
        >
          <ShieldCheck size={17} aria-hidden="true" />
          <span>{pending ? t("states.working") : t("auth.setup_action")}</span>
        </button>
      </form>
    </main>
  );
}
