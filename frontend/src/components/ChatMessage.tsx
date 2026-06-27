import type { ReactNode } from "react";

type ChatMessageProps = {
  children: ReactNode;
  meta?: string;
  tone?: "assistant" | "user" | "system";
};

export function ChatMessage({ children, meta, tone = "assistant" }: ChatMessageProps) {
  return (
    <article className="chat-message" data-tone={tone}>
      <div className="chat-bubble">
        {meta ? <p className="chat-meta">{meta}</p> : null}
        <div className="chat-copy">{children}</div>
      </div>
    </article>
  );
}
