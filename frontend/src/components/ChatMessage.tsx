import type { ReactNode } from "react";

import { MessageAttachments } from "@/components/MessageAttachments";
import type { Attachment } from "@/lib/types";

type ChatMessageProps = {
  attachments?: Attachment[];
  children: ReactNode;
  failed?: boolean;
  failedLabel?: string;
  messageId?: string;
  meta?: string;
  tone?: "assistant" | "user" | "system";
};

export function ChatMessage({
  attachments = [],
  children,
  failed = false,
  failedLabel,
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
        <div className="chat-copy">{children}</div>
        <MessageAttachments attachments={attachments} />
        {failed && failedLabel ? (
          <p className="message-failed-status" role="status">{failedLabel}</p>
        ) : null}
      </div>
    </article>
  );
}
