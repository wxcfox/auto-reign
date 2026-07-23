"use client";

import { Loader2, Paperclip, RotateCcw, X } from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

import { useTranslation } from "@/hooks/useTranslation";
import {
  deleteSubtaskContextDraft,
  uploadSubtaskContext,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { getErrorMessage } from "@/lib/error-messages";
import { MAX_ATTACHMENTS_PER_MESSAGE } from "@/lib/limits";
import type { SubtaskContextBrief } from "@/lib/types";

type AttachmentPickerProps = {
  children?: ReactNode;
  disabled: boolean;
  loading: boolean;
  onChange: Dispatch<SetStateAction<SubtaskContextBrief[]>>;
  onPendingChange: (pending: boolean) => void;
  onRetry: () => void;
  recoveryError: string | null;
  value: SubtaskContextBrief[];
};

export function AttachmentPicker({
  children,
  disabled,
  loading,
  onChange,
  onPendingChange,
  onRetry,
  recoveryError,
  value,
}: AttachmentPickerProps) {
  const { t } = useTranslation("chat");
  const inputRef = useRef<HTMLInputElement>(null);
  const mountedRef = useRef(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (loading) setError(null);
  }, [loading]);

  function mutationError(cause: unknown, fallbackKey: string) {
    return cause instanceof ApiError
      ? getErrorMessage(cause, t, fallbackKey)
      : t(fallbackKey);
  }

  async function handleFiles(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const files = Array.from(input.files ?? []);
    input.value = "";
    if (files.length === 0 || disabled || loading || busy) return;
    if (value.length + files.length > MAX_ATTACHMENTS_PER_MESSAGE) {
      setError(t("contexts.tooMany", { count: MAX_ATTACHMENTS_PER_MESSAGE }));
      return;
    }

    setBusy(true);
    setError(null);
    onPendingChange(true);
    try {
      for (const file of files) {
        const uploaded = await uploadSubtaskContext(file);
        if (!mountedRef.current) return;
        onChange((current) =>
          current.some((item) => item.id === uploaded.id)
            ? current
            : [...current, uploaded],
        );
      }
    } catch (cause) {
      if (mountedRef.current) {
        setError(mutationError(cause, "contexts.uploadFailed"));
      }
    } finally {
      if (mountedRef.current) {
        setBusy(false);
        onPendingChange(false);
      }
    }
  }

  async function handleRemove(context: SubtaskContextBrief) {
    if (disabled || loading || busy) return;
    setBusy(true);
    setError(null);
    onPendingChange(true);
    try {
      await deleteSubtaskContextDraft(context.id);
      if (mountedRef.current) {
        onChange((current) => current.filter((item) => item.id !== context.id));
      }
    } catch (cause) {
      if (mountedRef.current) {
        setError(mutationError(cause, "contexts.removeFailed"));
      }
    } finally {
      if (mountedRef.current) {
        setBusy(false);
        onPendingChange(false);
      }
    }
  }

  const unavailable = disabled || loading || busy || recoveryError !== null;
  const uploadUnavailable = unavailable || value.length >= MAX_ATTACHMENTS_PER_MESSAGE;
  const overLimit = value.length > MAX_ATTACHMENTS_PER_MESSAGE;

  return (
    <div className="attachment-picker">
      <div className="attachment-picker__actions">
        <input
          aria-label={t("contexts.fileInput")}
          disabled={uploadUnavailable}
          hidden
          multiple
          onChange={(event) => void handleFiles(event)}
          ref={inputRef}
          type="file"
        />
        <button
          aria-label={t("contexts.add")}
          className="composer-icon-button"
          disabled={uploadUnavailable}
          onClick={() => inputRef.current?.click()}
          type="button"
        >
          {busy ? (
            <Loader2 aria-hidden="true" className="attachment-spinner" size={17} />
          ) : (
            <Paperclip aria-hidden="true" size={17} />
          )}
        </button>
        {children}
      </div>

      {loading ? <span role="status">{t("contexts.loading")}</span> : null}
      {recoveryError ? (
        <div className="attachment-picker__error">
          <span role="alert">{recoveryError}</span>
          <button
            aria-label={t("contexts.retry")}
            disabled={busy}
            onClick={onRetry}
            type="button"
          >
            <RotateCcw aria-hidden="true" size={14} />
            {t("contexts.retryShort")}
          </button>
        </div>
      ) : null}
      {overLimit ? (
        <span role="alert">{t("contexts.tooMany", { count: MAX_ATTACHMENTS_PER_MESSAGE })}</span>
      ) : error ? <span role="alert">{error}</span> : null}

      {!loading && !recoveryError && value.length === 0 ? (
        <span className="sr-only">{t("contexts.empty")}</span>
      ) : null}

      {value.length > 0 ? (
        <ul aria-label={t("contexts.drafts")} className="attachment-drafts">
          {value.map((context) => (
            <li data-status={context.status} key={context.id}>
              <span title={context.name}>{context.name}</span>
              <small>{t(`contexts.status.${context.status}`)}</small>
              <button
                aria-label={t("contexts.remove", { name: context.name })}
                disabled={unavailable}
                onClick={() => void handleRemove(context)}
                type="button"
              >
                <X aria-hidden="true" size={13} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
