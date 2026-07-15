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
import { deleteAttachmentDraft, uploadAttachment } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { getErrorMessage } from "@/lib/error-messages";
import { MAX_ATTACHMENTS_PER_MESSAGE } from "@/lib/limits";
import type { Attachment } from "@/lib/types";

type AttachmentPickerProps = {
  children?: ReactNode;
  disabled: boolean;
  loading: boolean;
  onChange: Dispatch<SetStateAction<Attachment[]>>;
  onPendingChange: (pending: boolean) => void;
  onRetry: () => void;
  recoveryError: string | null;
  value: Attachment[];
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
    if (loading) {
      setError(null);
    }
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
    if (files.length === 0 || disabled || loading || busy) {
      return;
    }
    if (value.length + files.length > MAX_ATTACHMENTS_PER_MESSAGE) {
      setError(t("attachments.tooMany", { count: MAX_ATTACHMENTS_PER_MESSAGE }));
      return;
    }

    setBusy(true);
    setError(null);
    onPendingChange(true);
    try {
      for (const file of files) {
        const uploaded = await uploadAttachment(file);
        if (!mountedRef.current) {
          return;
        }
        onChange((current) =>
          current.some((item) => item.id === uploaded.id) ? current : [...current, uploaded],
        );
      }
    } catch (cause) {
      if (mountedRef.current) {
        setError(mutationError(cause, "attachments.uploadFailed"));
      }
    } finally {
      if (mountedRef.current) {
        setBusy(false);
        onPendingChange(false);
      }
    }
  }

  async function handleRemove(attachment: Attachment) {
    if (disabled || loading || busy) {
      return;
    }
    setBusy(true);
    setError(null);
    onPendingChange(true);
    try {
      await deleteAttachmentDraft(attachment.id);
      if (mountedRef.current) {
        onChange((current) => current.filter((item) => item.id !== attachment.id));
      }
    } catch (cause) {
      if (mountedRef.current) {
        setError(mutationError(cause, "attachments.removeFailed"));
      }
    } finally {
      if (mountedRef.current) {
        setBusy(false);
        onPendingChange(false);
      }
    }
  }

  const unavailable = disabled || loading || busy;
  const atLimit = value.length >= MAX_ATTACHMENTS_PER_MESSAGE;
  const overLimit = value.length > MAX_ATTACHMENTS_PER_MESSAGE;

  return (
    <div className="attachment-picker">
      <input
        accept=".md,.txt,.pdf,.docx,image/png,image/jpeg,image/webp,image/gif"
        aria-label={t("attachments.add")}
        className="sr-only"
        disabled={unavailable || atLimit}
        multiple
        onChange={(event) => void handleFiles(event)}
        ref={inputRef}
        type="file"
      />
      <button
        className="icon-button attachment-picker__trigger"
        disabled={unavailable || atLimit}
        onClick={() => inputRef.current?.click()}
        title={t("attachments.add")}
        type="button"
      >
        {busy || loading ? (
          <Loader2 aria-hidden="true" className="attachment-spinner" size={18} />
        ) : (
          <Paperclip aria-hidden="true" size={18} />
        )}
        <span className="sr-only">{t("attachments.add")}</span>
      </button>

      {children}

      {loading ? (
        <span className="attachment-picker__status" role="status">
          {t("attachments.loading")}
        </span>
      ) : null}
      {recoveryError ? (
        <div className="attachment-picker__error">
          <span role="alert">{recoveryError}</span>
          <button
            aria-label={t("attachments.retry")}
            className="attachment-picker__retry"
            disabled={busy}
            onClick={onRetry}
            type="button"
          >
            <RotateCcw aria-hidden="true" size={14} />
            {t("attachments.retryShort")}
          </button>
        </div>
      ) : null}
      {overLimit ? (
        <span className="attachment-picker__error-text" role="alert">
          {t("attachments.tooMany", { count: MAX_ATTACHMENTS_PER_MESSAGE })}
        </span>
      ) : error ? (
        <div className="attachment-picker__error">
          <span role="alert">{error}</span>
          <button
            aria-label={t("attachments.retry")}
            className="attachment-picker__retry"
            onClick={onRetry}
            type="button"
          >
            <RotateCcw aria-hidden="true" size={14} />
            {t("attachments.retryShort")}
          </button>
        </div>
      ) : null}

      {!loading && !recoveryError && value.length === 0 ? (
        <span className="sr-only">{t("attachments.empty")}</span>
      ) : null}

      {value.length > 0 ? (
        <ul aria-label={t("attachments.drafts")} className="attachment-drafts">
          {value.map((attachment) => (
            <li key={attachment.id}>
              <span title={attachment.filename}>{attachment.filename}</span>
              <button
                aria-label={t("attachments.remove", { name: attachment.filename })}
                disabled={unavailable}
                onClick={() => void handleRemove(attachment)}
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
