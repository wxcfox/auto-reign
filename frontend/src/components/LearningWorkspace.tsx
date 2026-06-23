"use client";

import { ChevronDown, Loader2, Paperclip, Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { useTranslation } from "@/hooks/useTranslation";
import { getModels, recordLearningNoteStream } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { LearningNoteResponse, ModelProvider, ProviderName } from "@/lib/types";

type LearningMessage = {
  id: string;
  tone: "assistant" | "user" | "system";
  meta: string;
  content: string;
  artifactPath?: string;
};

type ChatMessageProps = {
  children: ReactNode;
  meta?: string;
  tone?: "assistant" | "user" | "system";
};

const defaultModel = {
  provider: "qwen" as ProviderName,
  model: "qwen3.7-plus",
};

function ChatMessage({ children, meta, tone = "assistant" }: ChatMessageProps) {
  return (
    <article className="chat-message" data-tone={tone}>
      <div className="chat-bubble">
        {meta ? <p className="chat-meta">{meta}</p> : null}
        <div className="chat-copy">{children}</div>
      </div>
    </article>
  );
}

function responseToMarkdown(response: LearningNoteResponse, language: "en" | "zh-CN") {
  const labels = language === "zh-CN"
    ? {
        myUnderstanding: "我的理解",
        correction: "修正/补充",
        interviewExpression: "30 秒面试说法",
        confusion: "易混点",
        followUpQuestions: "追问",
        inboxSaved: `原始记录已保存到 ${response.source.relative_path}。`,
        noConfusion: "暂无明确易混点，后续练习中补充。",
      }
    : {
        myUnderstanding: "My understanding",
        correction: "Correction / supplement",
        interviewExpression: "30-second interview answer",
        confusion: "Common confusion",
        followUpQuestions: "Follow-up questions",
        inboxSaved: `Original note saved to ${response.source.relative_path}.`,
        noConfusion: "No clear confusion point yet; add one after practice.",
      };
  const correctionItems = uniqueItems([response.summary.summary, ...response.summary.key_points]);
  const interviewItems = response.summary.interview_takeaways.length > 0
    ? response.summary.interview_takeaways
    : [response.summary.summary];
  const followUpItems = response.summary.follow_up_questions.length > 0
    ? response.summary.follow_up_questions
    : [
        language === "zh-CN"
          ? "这个知识点在真实项目中如何落地？"
          : "How would this topic apply in a real project?",
      ];
  return [
    `# ${response.summary.title}`,
    `### ${response.summary.title}`,
    `- ${labels.myUnderstanding}：${labels.inboxSaved}`,
    `- ${labels.correction}：\n${correctionItems.map((item) => `  - ${item}`).join("\n")}`,
    `- ${labels.interviewExpression}：\n${interviewItems.map((item) => `  - ${item}`).join("\n")}`,
    `- ${labels.confusion}：\n  - ${labels.noConfusion}`,
    `- ${labels.followUpQuestions}：\n${followUpItems.map((item) => `  - ${item}`).join("\n")}`,
  ].join("\n\n");
}

function uniqueItems(items: string[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    const stripped = item.trim();
    if (!stripped || seen.has(stripped)) {
      return false;
    }
    seen.add(stripped);
    return true;
  });
}

export function LearningWorkspace() {
  const { t, getCurrentLanguage } = useTranslation("learning");
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<ProviderName>(defaultModel.provider);
  const [selectedModel, setSelectedModel] = useState(defaultModel.model);
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
        const preferred = response.providers.find(
          (provider) =>
            provider.provider === defaultModel.provider
            && provider.models.includes(defaultModel.model),
        );
        const fallback = response.providers[0];
        if (preferred) {
          setSelectedProvider(preferred.provider);
          setSelectedModel(defaultModel.model);
        } else if (fallback) {
          setSelectedProvider(fallback.provider);
          setSelectedModel(fallback.models[0] ?? "");
        }
      })
      .catch((loadError) => setError(getErrorMessage(loadError, t, "learning:errors.load")));
    return () => {
      cancelled = true;
    };
  }, []);

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
          text,
          language: responseLanguage,
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
            <div className="model-picker" data-open={modelMenuOpen}>
              <button
                aria-expanded={modelMenuOpen}
                aria-label={t("select_model")}
                className="model-picker-button"
                disabled={busy || providers.length === 0}
                onClick={() => setModelMenuOpen((current) => !current)}
                type="button"
              >
                <span>{selectedModel || t("model_unavailable")}</span>
                <ChevronDown aria-hidden="true" size={14} />
              </button>
              {modelMenuOpen ? (
                <div className="model-picker-menu" role="listbox" aria-label={t("select_model")}>
                  {providers.length === 0 ? (
                    <span className="model-picker-empty">{t("no_providers")}</span>
                  ) : null}
                  {providers.map((provider) => (
                    <div className="model-picker-group" key={provider.provider}>
                      <p>{provider.provider}</p>
                      {provider.models.map((model) => {
                        const active = provider.provider === selectedProvider && model === selectedModel;
                        return (
                          <button
                            aria-selected={active}
                            data-active={active}
                            key={`${provider.provider}-${model}`}
                            onClick={() => selectModel(provider.provider, model)}
                            role="option"
                            type="button"
                          >
                            {model}
                          </button>
                        );
                      })}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
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
