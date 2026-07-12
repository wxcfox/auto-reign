"use client";

import { Loader2, Paperclip, Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { ChatMessage } from "@/components/ChatMessage";
import { MarkdownView } from "@/components/MarkdownView";
import { ModelPicker } from "@/components/ModelPicker";
import { useTranslation } from "@/hooks/useTranslation";
import { getConversation, getModels, recordLearningNoteStream } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import { notifyConversationsChanged } from "@/lib/conversation-events";
import type {
  ConversationMessage,
  LearningNoteResponse,
  ModelProvider,
  ProviderName,
} from "@/lib/types";

type LearningMessage = {
  id: string;
  tone: "assistant" | "user" | "system";
  meta: string;
  content: string;
  artifactPath?: string;
};

type LearningWorkspaceProps = {
  sessionId?: string;
};

function responseToMarkdown(response: LearningNoteResponse, language: "en" | "zh-CN") {
  const title = response.summary.title.trim()
    || (language === "zh-CN" ? "学习记录" : "Learning note");
  return `# ${title}\n\n${response.card_markdown.trim()}`;
}

function conversationMessageToLearningMessage(
  message: ConversationMessage,
  translate: (key: string) => string,
): LearningMessage {
  const artifactPath =
    typeof message.metadata.artifact_path === "string" ? message.metadata.artifact_path : undefined;
  return {
    id: message.id,
    tone: message.role,
    meta:
      message.role === "user"
        ? translate("message_meta")
        : message.role === "assistant"
          ? translate("summary_meta")
          : translate("error_title"),
    content: message.content,
    artifactPath,
  };
}

export function LearningWorkspace({ sessionId }: LearningWorkspaceProps = {}) {
  const { t, getCurrentLanguage } = useTranslation("learning");
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<ProviderName | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(sessionId ?? null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [messages, setMessages] = useState<LearningMessage[]>([]);
  const [composerValue, setComposerValue] = useState("");
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    getModels()
      .then((response) => {
        if (cancelled) {
          return;
        }
        setProviders(response.providers);
        if (response.default) {
          setSelectedProvider(response.default.provider);
          setSelectedModel(response.default.model);
        } else {
          setSelectedProvider(null);
          setSelectedModel("");
        }
      })
      .catch((loadError) => setError(getErrorMessage(loadError, t, "learning:errors.load")));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setConversationId(null);
      return;
    }
    let cancelled = false;
    setBusy(true);
    setError(null);
    getConversation(sessionId)
      .then((conversation) => {
        if (cancelled) {
          return;
        }
        setConversationId(conversation.id);
        setMessages(
          conversation.messages.map((message) =>
            conversationMessageToLearningMessage(message, (key) => t(key)),
          ),
        );
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(getErrorMessage(loadError, t, "learning:errors.load"));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBusy(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, t]);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!transcript) {
      return;
    }
    transcript.scrollTo?.({ top: transcript.scrollHeight, behavior: "smooth" });
    transcript.scrollTop = transcript.scrollHeight;
  }, [messages.length, streamingText, busy]);

  const activeProvider = useMemo(
    () => providers.find((provider) => provider.provider === selectedProvider),
    [providers, selectedProvider],
  );
  const selectedModelAvailable = activeProvider?.models.includes(selectedModel) ?? false;
  const canSend = Boolean(composerValue.trim()) && selectedModelAvailable && !busy;

  function selectModel(provider: ProviderName, model: string) {
    setSelectedProvider(provider);
    setSelectedModel(model);
    setModelMenuOpen(false);
  }

  async function handleSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const text = composerValue.trim();
    if (!text || !selectedModelAvailable || busy) {
      return;
    }

    const responseLanguage = getCurrentLanguage() === "zh-CN" ? "zh-CN" : "en";
    let streamed = "";
    const userMessage: LearningMessage = {
      id: `user-${Date.now()}`,
      tone: "user",
      meta: t("message_meta"),
      content: text,
    };
    setMessages((current) => [...current, userMessage]);
    setComposerValue("");
    setStreamingText("");
    setBusy(true);
    setError(null);
    try {
      const response = await recordLearningNoteStream(
        {
          conversation_id: conversationId ?? undefined,
          text,
          language: responseLanguage,
          provider: selectedProvider ?? undefined,
          model: selectedModel,
        },
        {
          onDelta: (delta) => {
            streamed += delta;
            setStreamingText(streamed);
          },
        },
      );
      setConversationId(response.conversation_id);
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          tone: "assistant",
          meta: t("summary_meta"),
          content: responseToMarkdown(response, responseLanguage),
          artifactPath: response.artifact.relative_path,
        },
      ]);
      notifyConversationsChanged();
    } catch (recordError) {
      setError(getErrorMessage(recordError, t, "learning:errors.record"));
    } finally {
      setStreamingText(null);
      setBusy(false);
    }
  }

  return (
    <div className="chat-workspace learning-workspace">
      <header className="chat-topbar">
        <span className="chat-topbar-title">{messages.length > 0 ? t("title") : t("not_started")}</span>
        <span className="chat-topbar-model">{selectedModel || t("model_unavailable")}</span>
      </header>

      <div className="chat-transcript" ref={transcriptRef}>
        {messages.length === 0 && streamingText === null ? (
          <section className="chat-empty" aria-label={t("empty_label")}>
            <h2>{t("empty_title")}</h2>
            <p>{t("empty_body")}</p>
          </section>
        ) : (
          <div className="chat-thread">
            {messages.map((message) => (
              <ChatMessage key={message.id} meta={message.meta} tone={message.tone}>
                {message.tone === "assistant" ? (
                  <>
                    <MarkdownView content={message.content} />
                    {message.artifactPath ? (
                      <p className="saved-artifact">{t("saved_to", { path: message.artifactPath })}</p>
                    ) : null}
                  </>
                ) : (
                  <p>{message.content}</p>
                )}
              </ChatMessage>
            ))}
            {streamingText !== null ? (
              <ChatMessage meta={t("streaming.label")}>
                {streamingText ? (
                  <MarkdownView content={streamingText} />
                ) : (
                  <p className="typing-line">
                    <Loader2 aria-hidden="true" size={16} />
                    {t("streaming.body")}
                  </p>
                )}
              </ChatMessage>
            ) : null}
          </div>
        )}

        {error ? (
          <ChatMessage meta={t("error_title")} tone="system">
            <p className="form-error" role="alert">
              {error}
            </p>
          </ChatMessage>
        ) : null}
      </div>

      <div className="chat-composer-wrap">
        <form className="chat-composer" aria-label={t("composer.label")} onSubmit={handleSubmit}>
          <div className="composer-box">
            <button className="icon-button" disabled type="button" aria-label={t("composer.attach")}>
              <Paperclip aria-hidden="true" size={18} />
            </button>
            <label className="sr-only" htmlFor="learning-composer">
              {t("composer.input_label")}
            </label>
            <textarea
              aria-label={t("composer.input_label")}
              disabled={busy}
              id="learning-composer"
              onChange={(event) => setComposerValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void handleSubmit();
                }
              }}
              placeholder={t("composer.placeholder")}
              rows={1}
              value={composerValue}
            />
            <ModelPicker
              disabled={busy}
              labels={{
                listbox: t("select_model"),
                modelUnavailable: t("model_unavailable"),
                noProviders: t("no_providers"),
                selectModel: t("select_model"),
              }}
              onOpenChange={setModelMenuOpen}
              onSelect={selectModel}
              open={modelMenuOpen}
              providers={providers}
              selectedModel={selectedModel}
              selectedProvider={selectedProvider}
            />
            <button
              aria-label={t("composer.send")}
              className="send-button"
              disabled={!canSend}
              type="submit"
            >
              {busy ? <Loader2 aria-hidden="true" size={18} /> : <Send aria-hidden="true" size={18} />}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
