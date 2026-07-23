import type { ReactNode } from "react";

import { MessageBlocks } from "@/components/chat/MessageBlocks";

type ChatMessageProps = {
  blocks?: readonly unknown[];
  children?: ReactNode;
  failed?: boolean;
  failedLabel?: string;
  footer?: ReactNode;
  messageId?: string;
  meta?: string;
  tone?: "assistant" | "user" | "system";
};

export function ChatMessage({
  blocks,
  children,
  failed = false,
  failedLabel,
  footer,
  messageId,
  meta,
  tone = "assistant",
}: ChatMessageProps) {
  return (
    <article
      className={`chat-message${failed ? " message-failed" : ""}`}
      data-message-id={messageId}
      data-status={failed ? "failed" : undefined}
      data-tone={tone}
    >
      <div className="chat-bubble">
        {meta ? <p className="chat-meta">{meta}</p> : null}
        <div className="chat-copy">
          {blocks ? <MessageBlocks blocks={blocks} /> : children}
        </div>
        {failed && failedLabel ? (
          <p className="message-failed-status" role="status">{failedLabel}</p>
        ) : null}
        {footer}
      </div>
    </article>
  );
}
