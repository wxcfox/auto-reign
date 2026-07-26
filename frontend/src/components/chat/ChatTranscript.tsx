"use client";

import { Loader2, RotateCcw } from "lucide-react";
import type { ReactNode, RefObject } from "react";

import { ChatMessage } from "@/components/ChatMessage";
import { SubtaskContexts } from "@/components/SubtaskContexts";

import type { TaskChatMessage } from "./task-chat-reducer";

export type ChatTranscriptLabels = {
  assistant: string;
  emptyLabel: string;
  errorTitle: string;
  failedResponse: string;
  retry: string;
  retrying: string;
  streaming: string;
  you: string;
};

export type ChatTranscriptProps = {
  composer: ReactNode;
  emptyTitle: string;
  error: string | null;
  labels: ChatTranscriptLabels;
  messages: TaskChatMessage[];
  onRetry: (subtaskId: number) => void;
  retryDisabled: boolean;
  retryingSubtaskId: number | null;
  transcriptRef: RefObject<HTMLDivElement | null>;
};

export function ChatTranscript({
  composer,
  emptyTitle,
  error,
  labels,
  messages,
  onRetry,
  retryDisabled,
  retryingSubtaskId,
  transcriptRef,
}: ChatTranscriptProps) {
  const isEmpty = messages.length === 0;

  return (
    <div className="chat-transcript" ref={transcriptRef}>
      {isEmpty ? (
        <section aria-label={labels.emptyLabel} className="chat-empty">
          <h2>{emptyTitle}</h2>
          <div className="chat-empty-composer">{composer}</div>
        </section>
      ) : (
        <div className="chat-thread">
          {messages.map((message) => (
            <ChatTranscriptMessage
              key={message.key}
              labels={labels}
              message={message}
              onRetry={onRetry}
              retryDisabled={retryDisabled}
              retryingSubtaskId={retryingSubtaskId}
            />
          ))}
        </div>
      )}
      {error ? (
        <ChatMessage meta={labels.errorTitle} tone="system">
          <p className="form-error" role="alert">{error}</p>
        </ChatMessage>
      ) : null}
    </div>
  );
}

type ChatTranscriptMessageProps = {
  labels: ChatTranscriptLabels;
  message: TaskChatMessage;
  onRetry: (subtaskId: number) => void;
  retryDisabled: boolean;
  retryingSubtaskId: number | null;
};

function ChatTranscriptMessage({
  labels,
  message,
  onRetry,
  retryDisabled,
  retryingSubtaskId,
}: ChatTranscriptMessageProps) {
  const isUser = message.role === "USER";
  const failed = message.role === "ASSISTANT" && message.status === "FAILED";
  const streaming =
    message.role === "ASSISTANT" &&
    message.blocks.length === 0 &&
    (message.status === "PENDING" || message.status === "RUNNING");
  const subtaskId = message.subtaskId;

  return (
    <ChatMessage
      blocks={
        message.role === "ASSISTANT" && message.blocks.length > 0 ? message.blocks : undefined
      }
      failed={failed}
      failedLabel={failed ? labels.failedResponse : undefined}
      footer={
        failed && subtaskId !== null ? (
          <button disabled={retryDisabled} onClick={() => onRetry(subtaskId)} type="button">
            <RotateCcw aria-hidden="true" size={14} />
            {retryingSubtaskId === subtaskId ? labels.retrying : labels.retry}
          </button>
        ) : null
      }
      messageId={subtaskId === null ? message.key : String(subtaskId)}
      meta={isUser ? labels.you : labels.assistant}
      tone={isUser ? "user" : "assistant"}
    >
      {isUser ? <p>{message.prompt}</p> : null}
      {streaming ? (
        <p className="typing-line">
          <Loader2 aria-hidden="true" size={16} />
          {labels.streaming}
        </p>
      ) : null}
      {isUser ? <SubtaskContexts contexts={message.contexts} /> : null}
    </ChatMessage>
  );
}
