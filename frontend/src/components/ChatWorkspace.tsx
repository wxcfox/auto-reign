"use client";

import { Loader2, Paperclip, Send } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { AutoResizeTextarea } from "@/components/AutoResizeTextarea";
import { ChatMessage } from "@/components/ChatMessage";
import { MarkdownView } from "@/components/MarkdownView";
import { ModelPicker } from "@/components/ModelPicker";
import { useTranslation } from "@/hooks/useTranslation";
import { getConversation, getModels, sendChatMessageStream } from "@/lib/api";
import { notifyConversationsChanged } from "@/lib/conversation-events";
import { getErrorMessage } from "@/lib/error-messages";
import type { ConversationMessage, ModelProvider, ProviderName } from "@/lib/types";

type ChatWorkspaceProps = {
  sessionId?: string;
};

export function ChatWorkspace({ sessionId }: ChatWorkspaceProps = {}) {
  const router = useRouter();
  const { t, getCurrentLanguage } = useTranslation("chat");
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<ProviderName | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(sessionId ?? null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [composerValue, setComposerValue] = useState("");
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
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
        setSelectedProvider(response.default?.provider ?? null);
        setSelectedModel(response.default?.model ?? "");
      })
      .catch((loadError) => setError(getErrorMessage(loadError, t, "chat:errors.load")));
    return () => {
      cancelled = true;
    };
  }, [t]);

  useEffect(() => {
    if (!sessionId) {
      setConversationId(null);
      setMessages([]);
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
        if (conversation.kind !== "chat") {
          throw new Error("conversation_kind_mismatch");
        }
        setConversationId(conversation.id);
        setMessages(conversation.messages);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(getErrorMessage(loadError, t, "chat:errors.load"));
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
    if (!text || !selectedProvider || !selectedModelAvailable || busy) {
      return;
    }
    const temporaryId = `user-${Date.now()}`;
    const userMessage: ConversationMessage = {
      id: temporaryId,
      role: "user",
      message_type: "chat_message",
      content: text,
      created_at: new Date().toISOString(),
      metadata: {},
    };
    let streamed = "";
    setMessages((current) => [...current, userMessage]);
    setComposerValue("");
    setStreamingText("");
    setBusy(true);
    setError(null);
    try {
      const result = await sendChatMessageStream(
        {
          text,
          conversation_id: conversationId ?? undefined,
          language: getCurrentLanguage() === "zh-CN" ? "zh-CN" : "en",
          provider: selectedProvider,
          model: selectedModel,
        },
        {
          onDelta: (delta) => {
            streamed += delta;
            setStreamingText(streamed);
          },
        },
      );
      setConversationId(result.conversation_id);
      setMessages((current) => [...current, result.message]);
      if (!conversationId) {
        router.replace(`/chat?session=${result.conversation_id}`, { scroll: false });
      }
      notifyConversationsChanged();
    } catch (sendError) {
      setMessages((current) => current.filter((message) => message.id !== temporaryId));
      setError(getErrorMessage(sendError, t, "chat:errors.send"));
    } finally {
      setStreamingText(null);
      setBusy(false);
    }
  }

  return (
    <div className="chat-workspace general-chat-workspace">
      <header className="chat-topbar">
        <span className="chat-topbar-title">{t("title")}</span>
        <span className="chat-topbar-model">{selectedModel || t("model_unavailable")}</span>
      </header>

      <div className="chat-transcript" ref={transcriptRef}>
        {messages.length === 0 && streamingText === null ? (
          <section className="chat-empty" aria-label={t("empty_label")}>
            <h2>{t("empty_title")}</h2>
          </section>
        ) : (
          <div className="chat-thread">
            {messages.map((message) => (
              <ChatMessage
                key={message.id}
                meta={message.role === "user" ? t("you") : t("assistant")}
                tone={message.role}
              >
                {message.role === "assistant" ? (
                  <MarkdownView content={message.content} />
                ) : (
                  <p>{message.content}</p>
                )}
              </ChatMessage>
            ))}
            {streamingText !== null ? (
              <ChatMessage meta={t("assistant")}>
                {streamingText ? (
                  <MarkdownView content={streamingText} />
                ) : (
                  <p className="typing-line">
                    <Loader2 aria-hidden="true" size={16} />
                    {t("streaming")}
                  </p>
                )}
              </ChatMessage>
            ) : null}
          </div>
        )}
        {error ? (
          <ChatMessage meta={t("error_title")} tone="system">
            <p className="form-error" role="alert">{error}</p>
          </ChatMessage>
        ) : null}
      </div>

      <div className="chat-composer-wrap">
        <form className="chat-composer" aria-label={t("composer.label")} onSubmit={handleSubmit}>
          <div className="composer-box">
            <button className="icon-button" disabled type="button" aria-label={t("composer.attach")}>
              <Paperclip aria-hidden="true" size={18} />
            </button>
            <label className="sr-only" htmlFor="chat-composer">{t("composer.input_label")}</label>
            <AutoResizeTextarea
              aria-label={t("composer.input_label")}
              disabled={busy}
              id="chat-composer"
              onChange={(event) => setComposerValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void handleSubmit();
                }
              }}
              placeholder={t("composer.placeholder")}
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
            <button aria-label={t("composer.send")} className="send-button" disabled={!canSend} type="submit">
              {busy ? <Loader2 aria-hidden="true" size={18} /> : <Send aria-hidden="true" size={18} />}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
