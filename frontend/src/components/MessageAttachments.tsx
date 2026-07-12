"use client";

import { Download, Eye, FileText, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { readAttachmentContent } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { getErrorMessage } from "@/lib/error-messages";
import type { Attachment } from "@/lib/types";

type MessageAttachmentsProps = {
  attachments: Attachment[];
};

type ActiveAction = {
  attachmentId: string;
  disposition: "inline" | "attachment";
} | null;

export function MessageAttachments({ attachments }: MessageAttachmentsProps) {
  const { t } = useTranslation("chat");
  const mountedRef = useRef(true);
  const attachmentViewRef = useRef(0);
  const [active, setActive] = useState<ActiveAction>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const attachmentSignature = attachments.map((attachment) => attachment.id).join("\u0000");
  useEffect(() => {
    attachmentViewRef.current += 1;
  }, [attachmentSignature]);

  async function openAttachment(
    attachment: Attachment,
    disposition: "inline" | "attachment",
  ) {
    if (active) {
      return;
    }
    setActive({ attachmentId: attachment.id, disposition });
    setError(null);
    const attachmentView = attachmentViewRef.current;
    const previewWindow = disposition === "inline"
      ? window.open("about:blank", "_blank")
      : null;
    if (previewWindow) {
      previewWindow.opener = null;
    }
    let objectUrl: string | null = null;
    try {
      const blob = await readAttachmentContent(attachment.id, disposition);
      if (!mountedRef.current || attachmentViewRef.current !== attachmentView) {
        previewWindow?.close();
        return;
      }
      objectUrl = URL.createObjectURL(blob);
      if (disposition === "inline") {
        if (previewWindow) {
          previewWindow.location.replace(objectUrl);
        } else {
          window.open(objectUrl, "_blank", "noopener,noreferrer");
        }
        const urlToRevoke = objectUrl;
        objectUrl = null;
        window.setTimeout(() => URL.revokeObjectURL(urlToRevoke), 60_000);
      } else {
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = attachment.filename;
        anchor.rel = "noopener";
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        const urlToRevoke = objectUrl;
        objectUrl = null;
        window.setTimeout(() => URL.revokeObjectURL(urlToRevoke), 0);
      }
    } catch (cause) {
      previewWindow?.close();
      if (mountedRef.current && attachmentViewRef.current === attachmentView) {
        setError(
          cause instanceof ApiError
            ? getErrorMessage(cause, t, "attachments.readFailed")
            : t("attachments.readFailed"),
        );
      }
    } finally {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
      if (mountedRef.current) {
        setActive(null);
      }
    }
  }

  if (attachments.length === 0) {
    return null;
  }

  return (
    <div className="message-attachments">
      <ul aria-label={t("attachments.messageFiles")}>
        {attachments.map((attachment) => {
          const loading = active?.attachmentId === attachment.id;
          return (
            <li key={attachment.id}>
              <FileText aria-hidden="true" size={15} />
              <span className="message-attachment__name" title={attachment.filename}>
                {attachment.filename}
              </span>
              <button
                aria-label={t("attachments.preview", { name: attachment.filename })}
                disabled={active !== null}
                onClick={() => void openAttachment(attachment, "inline")}
                type="button"
              >
                {loading && active?.disposition === "inline" ? (
                  <Loader2 aria-hidden="true" className="attachment-spinner" size={14} />
                ) : (
                  <Eye aria-hidden="true" size={14} />
                )}
              </button>
              <button
                aria-label={t("attachments.download", { name: attachment.filename })}
                disabled={active !== null}
                onClick={() => void openAttachment(attachment, "attachment")}
                type="button"
              >
                {loading && active?.disposition === "attachment" ? (
                  <Loader2 aria-hidden="true" className="attachment-spinner" size={14} />
                ) : (
                  <Download aria-hidden="true" size={14} />
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {error ? <p className="message-attachments__error" role="alert">{error}</p> : null}
    </div>
  );
}
