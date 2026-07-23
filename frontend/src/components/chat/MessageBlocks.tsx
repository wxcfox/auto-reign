"use client";

import { MarkdownView } from "@/components/MarkdownView";
import { useTranslation } from "@/hooks/useTranslation";

interface MessageBlocksProps {
  blocks: readonly unknown[];
}

const toolStatuses = new Set([
  "generating_arguments",
  "pending",
  "done",
  "error",
]);

export function MessageBlocks({ blocks }: MessageBlocksProps) {
  const { t } = useTranslation("chat");

  return (
    <div className="message-blocks">
      {blocks.map((rawBlock, index) => {
        const block = asRecord(rawBlock);
        const key = typeof block?.id === "string" ? block.id : `unknown-${index}`;
        if (block?.type === "text" && typeof block.content === "string") {
          return <MarkdownView content={block.content} key={key} />;
        }
        if (block?.type === "tool") {
          const name = typeof block.tool_name === "string"
            ? block.tool_name
            : t("tools.unknownName", { defaultValue: "Unknown tool" });
          const rawStatus = typeof block.status === "string" && toolStatuses.has(block.status)
            ? block.status
            : "unknown";
          const status = t(`tools.status.${rawStatus}`, {
            defaultValue: statusFallback(rawStatus),
          });
          const inputLabel = t("tools.input", { defaultValue: "Tool input" });
          const outputLabel = t("tools.output", { defaultValue: "Tool output" });
          const hasOutput = Object.prototype.hasOwnProperty.call(block, "tool_output");
          return (
            <details className="tool-block" data-status={rawStatus} data-testid={`tool-block-${key}`} key={key}>
              <summary>{name} · {status}</summary>
              <div className="tool-block-payload">
                <span className="tool-block-label">{inputLabel}</span>
                <pre aria-label={inputLabel}>{formatValue(block.tool_input ?? {})}</pre>
              </div>
              {hasOutput ? (
                <div className="tool-block-payload">
                  <span className="tool-block-label">{outputLabel}</span>
                  <pre aria-label={outputLabel}>{formatValue(block.tool_output)}</pre>
                </div>
              ) : null}
            </details>
          );
        }
        return (
          <p className="message-block-unknown" data-block-type="unknown" key={key}>
            {unknownBlockText(block, rawBlock, t("tools.unknownBlock", {
              defaultValue: "Unsupported message block",
            }))}
          </p>
        );
      })}
    </div>
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function unknownBlockText(
  block: Record<string, unknown> | null,
  rawBlock: unknown,
  fallback: string,
): string {
  if (typeof block?.content === "string") return block.content;
  const formatted = formatValue(rawBlock);
  return formatted === undefined || formatted === "{}" ? fallback : formatted;
}

function formatValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    const formatted = JSON.stringify(value, null, 2);
    return formatted ?? String(value);
  } catch {
    return "[Unserializable value]";
  }
}

function statusFallback(status: string): string {
  if (status === "generating_arguments") return "Preparing";
  return status.charAt(0).toUpperCase() + status.slice(1);
}
