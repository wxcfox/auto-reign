"use client";

import { Download, Eye, FileText, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { readSubtaskContextContent } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { getErrorMessage } from "@/lib/error-messages";
import type { SubtaskContextBrief } from "@/lib/types";

type SubtaskContextsProps = {
  contexts: SubtaskContextBrief[];
};

type ActiveAction = {
  contextId: number;
  disposition: "inline" | "attachment";
} | null;

export function SubtaskContexts({ contexts }: SubtaskContextsProps) {
  const { t } = useTranslation("chat");
  const mountedRef = useRef(true);
  const viewRef = useRef(0);
  const [active, setActive] = useState<ActiveAction>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      viewRef.current += 1;
    };
  }, []);

  const signature = contexts.map((context) => context.id).join(":");
  useEffect(() => {
    viewRef.current += 1;
    setActive(null);
    setError(null);
  }, [signature]);

  async function openContext(
    context: SubtaskContextBrief,
    disposition: "inline" | "attachment",
  ) {
    if (active || context.context_type !== "attachment" || context.status !== "ready") return;
    const view = viewRef.current;
    const previewWindow = disposition === "inline" ? window.open("about:blank", "_blank") : null;
    if (previewWindow) previewWindow.opener = null;
    setActive({ contextId: context.id, disposition });
    setError(null);
    let objectUrl: string | null = null;
    try {
      const blob = await readSubtaskContextContent(context.id, disposition);
      if (!mountedRef.current || viewRef.current !== view) {
        previewWindow?.close();
        return;
      }
      objectUrl = URL.createObjectURL(blob);
      if (disposition === "inline") {
        if (previewWindow) previewWindow.location.replace(objectUrl);
        else window.open(objectUrl, "_blank", "noopener,noreferrer");
        const url = objectUrl;
        objectUrl = null;
        window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } else {
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = context.name;
        anchor.rel = "noopener";
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        const url = objectUrl;
        objectUrl = null;
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
      }
    } catch (cause) {
      previewWindow?.close();
      if (mountedRef.current && viewRef.current === view) {
        setError(
          cause instanceof ApiError
            ? getErrorMessage(cause, t, "contexts.readFailed")
            : t("contexts.readFailed"),
        );
      }
    } finally {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (mountedRef.current && viewRef.current === view) setActive(null);
    }
  }

  if (contexts.length === 0) return null;

  return (
    <div className="message-attachments subtask-contexts">
      <ul aria-label={t("contexts.messageContexts")}>
        {contexts.map((context) => {
          const isAttachment = context.context_type === "attachment";
          const canRead = isAttachment && context.status === "ready";
          const loading = active?.contextId === context.id;
          return (
            <li data-context-type={context.context_type} data-status={context.status} key={context.id}>
              <FileText aria-hidden="true" size={15} />
              <span className="message-attachment__name" title={context.name}>{context.name}</span>
              <small>
                {t(`contexts.type.${context.context_type}`)} · {contextSummary(context, t)}
              </small>
              {Object.keys(context.type_data).length > 0 ? (
                <details>
                  <summary>{t("contexts.knowledgeContent")}</summary>
                  <pre>{JSON.stringify(context.type_data, null, 2)}</pre>
                </details>
              ) : null}
              {isAttachment ? (
                <>
                  <button
                    aria-label={t("contexts.preview", { name: context.name })}
                    disabled={active !== null || !canRead}
                    onClick={() => void openContext(context, "inline")}
                    type="button"
                  >
                    {loading && active?.disposition === "inline" ? (
                      <Loader2 aria-hidden="true" size={14} />
                    ) : <Eye aria-hidden="true" size={14} />}
                  </button>
                  <button
                    aria-label={t("contexts.download", { name: context.name })}
                    disabled={active !== null || !canRead}
                    onClick={() => void openContext(context, "attachment")}
                    type="button"
                  >
                    {loading && active?.disposition === "attachment" ? (
                      <Loader2 aria-hidden="true" size={14} />
                    ) : <Download aria-hidden="true" size={14} />}
                  </button>
                </>
              ) : null}
            </li>
          );
        })}
      </ul>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}

function contextSummary(
  context: SubtaskContextBrief,
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  const status = t(`contexts.status.${context.status}`);
  const size = context.file_size === null
    ? null
    : t("contexts.bytes", { count: context.file_size });
  const parsed = context.text_length > 0
    ? t("contexts.parsedText", { count: context.text_length })
    : t("contexts.noParsedText");
  return [status, context.mime_type, size, parsed].filter(Boolean).join(" · ");
}
