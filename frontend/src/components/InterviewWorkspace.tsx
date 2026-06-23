"use client";

import {
  CheckCircle2,
  ChevronDown,
  Loader2,
  Paperclip,
  RotateCcw,
  Send,
  Settings2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import {
  createInterviewSessionStream,
  finishInterviewStream,
  getInterviewSession,
  getLastInterviewConfig,
  getModels,
  nextQuestionStream,
  saveLastInterviewConfig,
  submitAnswerStream,
  submitFollowUpAnswerStream,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import { notifyInterviewSessionsChanged } from "@/lib/interview-events";
import type {
  AnswerFeedback,
  FollowUpFeedback,
  InterviewConfig,
  InterviewMode,
  InterviewSession,
  InterviewTurn,
  ModelProvider,
  ReportRecord,
} from "@/lib/types";

const defaultConfig: InterviewConfig = {
  target_company: "",
  target_role: "",
  job_description: "",
  extra_prompt: "",
  language: "en",
  mode: "comprehensive",
  chat_model_provider: "qwen",
  chat_model: "qwen3.7-plus",
  target_rounds: 3,
};

type ComposerMode = "start" | "answer" | "follow-up" | "idle";

type StreamingDraft = {
  kind: "question" | "feedback" | "follow-up-feedback" | "report";
  meta: string;
  text: string;
  turnId?: string;
};

type ChatMessageProps = {
  children: ReactNode;
  meta?: string;
  tone?: "assistant" | "user" | "system";
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

function updateTurn(
  turns: InterviewTurn[],
  turnId: string,
  updater: (turn: InterviewTurn) => InterviewTurn,
) {
  return turns.map((item) => (item.id === turnId ? updater(item) : item));
}

type InterviewWorkspaceProps = {
  sessionId?: string;
};

export function InterviewWorkspace({ sessionId }: InterviewWorkspaceProps = {}) {
  const { t, getCurrentLanguage, i18n } = useTranslation("interview");
  const [config, setConfig] = useState<InterviewConfig>(defaultConfig);
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [session, setSession] = useState<InterviewSession | null>(null);
  const [turns, setTurns] = useState<InterviewTurn[]>([]);
  const [report, setReport] = useState<ReportRecord | null>(null);
  const [composerValue, setComposerValue] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streamingDraft, setStreamingDraft] = useState<StreamingDraft | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [openingContext, setOpeningContext] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);

  const modeOptions: Array<{ value: InterviewMode; label: string }> = [
    { value: "comprehensive", label: t("mode_options.comprehensive") },
    { value: "project_deep_dive", label: t("mode_options.project_deep_dive") },
    { value: "knowledge_drill", label: t("mode_options.knowledge_drill") },
    { value: "weakness_reinforcement", label: t("mode_options.weakness_reinforcement") },
  ];

  useEffect(() => {
    setConfig((current) => ({
      ...current,
      language: getCurrentLanguage() === "zh-CN" ? "zh-CN" : "en",
    }));
  }, [i18n.language]);

  useEffect(() => {
    let cancelled = false;
    const configPromise = sessionId ? Promise.resolve(null) : getLastInterviewConfig();
    Promise.allSettled([getModels(), configPromise]).then(
      ([modelsResult, configResult]) => {
        if (cancelled) {
          return;
        }

        const availableProviders =
          modelsResult.status === "fulfilled" ? modelsResult.value.providers : [];

        if (modelsResult.status === "fulfilled") {
          setProviders(availableProviders);
        }
        if (configResult.status === "fulfilled" && configResult.value) {
          const { id: _id, is_last_used: _isLastUsed, updated_at: _updatedAt, ...lastConfig } =
            configResult.value;
          const matchedProvider = availableProviders.find(
            (provider) => provider.provider === lastConfig.chat_model_provider,
          );
          if (matchedProvider?.models.includes(lastConfig.chat_model)) {
            setConfig(lastConfig);
          } else if (availableProviders.length > 0) {
            setConfig((current) => ({
              ...current,
              ...lastConfig,
              chat_model_provider: availableProviders[0].provider,
              chat_model: availableProviders[0].models[0] ?? "",
            }));
          } else {
            setConfig(lastConfig);
          }
        } else if (availableProviders.length > 0 && !sessionId) {
          setConfig((current) => {
            const matchedProvider = availableProviders.find(
              (provider) => provider.provider === current.chat_model_provider,
            );
            if (matchedProvider?.models.includes(current.chat_model)) {
              return current;
            }
            return {
              ...current,
              chat_model_provider: availableProviders[0].provider,
              chat_model: availableProviders[0].models[0] ?? "",
            };
          });
        }
        if (modelsResult.status === "rejected" || configResult.status === "rejected") {
          const rejection = modelsResult.status === "rejected"
            ? modelsResult.reason
            : configResult.status === "rejected"
              ? configResult.reason
              : null;
          setError(getErrorMessage(rejection, t, "interview:errors.load"));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [sessionId, t]);

  useEffect(() => {
    if (!sessionId) {
      return;
    }
    let cancelled = false;
    setBusyAction("load");
    setError(null);
    getInterviewSession(sessionId)
      .then((detail) => {
        if (cancelled) {
          return;
        }
        const {
          id: _id,
          is_last_used: _isLastUsed,
          updated_at: _updatedAt,
          ...loadedConfig
        } = detail.config;
        setConfig(loadedConfig);
        setSession(detail.session);
        setTurns(detail.turns);
        setReport(null);
        setOpeningContext(loadedConfig.extra_prompt.trim() || null);
        setComposerValue("");
        setStreamingDraft(null);
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(getErrorMessage(loadError, t, "interview:errors.load"));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBusyAction(null);
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
    if (typeof transcript.scrollTo === "function") {
      transcript.scrollTo({
        top: transcript.scrollHeight,
        behavior: "smooth",
      });
    } else {
      transcript.scrollTop = transcript.scrollHeight;
    }
  }, [turns.length, busyAction, openingContext, streamingDraft?.text]);

  const selectedProvider = useMemo(
    () => providers.find((provider) => provider.provider === config.chat_model_provider),
    [config.chat_model_provider, providers],
  );
  const selectedModelAvailable =
    selectedProvider?.models.includes(config.chat_model) ?? false;
  const currentTurn = turns.at(-1) ?? null;
  const canStart =
    selectedModelAvailable &&
    !busyAction &&
    session?.status !== "active";
  const composerMode: ComposerMode = (() => {
    if (!session || session.status !== "active" || !currentTurn) {
      return session?.status === "completed" ? "idle" : "start";
    }
    if (!currentTurn.answer) {
      return "answer";
    }
    if (currentTurn.follow_up_question && !currentTurn.follow_up_answer) {
      return "follow-up";
    }
    return "idle";
  })();
  const canSend =
    !busyAction &&
    ((composerMode === "start" && canStart) ||
      (session?.status === "active" &&
        Boolean(composerValue.trim()) &&
        (composerMode === "answer" || composerMode === "follow-up")));
  const canRetryFinish =
    session?.status === "active" &&
    composerMode === "idle" &&
    currentTurn?.answer &&
    session.current_round >= config.target_rounds &&
    (!currentTurn.follow_up_question || currentTurn.follow_up_answer) &&
    !busyAction;

  function updateConfig<K extends keyof InterviewConfig>(field: K, value: InterviewConfig[K]) {
    setConfig((current) => ({ ...current, [field]: value }));
  }

  function appendStreamingDelta(text: string) {
    setStreamingDraft((current) => (current ? { ...current, text: current.text + text } : current));
  }

  async function handleStart(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!canStart) {
      return;
    }

    setBusyAction("start");
    setError(null);
    setReport(null);
    const startContext = composerValue.trim();
    setOpeningContext(startContext || null);
    setStreamingDraft({ kind: "question", meta: t("question", { index: 1 }), text: "" });
    try {
      const activeConfig: InterviewConfig = {
        ...config,
        target_company: "",
        target_role: "",
        job_description: "",
        extra_prompt: startContext,
        language: getCurrentLanguage() === "zh-CN" ? "zh-CN" : "en",
      };
      setConfig(activeConfig);
      await saveLastInterviewConfig(activeConfig);
      const created = await createInterviewSessionStream(activeConfig, {
        onDelta: appendStreamingDelta,
      });
      setSession(created.session);
      setTurns([created.turn]);
      notifyInterviewSessionsChanged();
      setComposerValue("");
      setShowAdvanced(false);
    } catch (startError) {
      setError(getErrorMessage(startError, t, "interview:errors.start"));
    } finally {
      setStreamingDraft(null);
      setBusyAction(null);
    }
  }

  async function handleAnswer() {
    if (!session || !currentTurn || !composerValue.trim()) {
      return;
    }
    const activeSession = session;
    const submittedAnswer = composerValue.trim();
    setComposerValue("");
    setBusyAction("answer");
    setError(null);
    setStreamingDraft({
      kind: "feedback",
      meta: t("feedback"),
      text: "",
      turnId: currentTurn.id,
    });
    setTurns((current) =>
      updateTurn(current, currentTurn.id, (item) => ({
        ...item,
        answer: submittedAnswer,
      })),
    );
    try {
      const response = await submitAnswerStream(session.id, submittedAnswer, {
        onDelta: appendStreamingDelta,
      });
      applyMainFeedback(currentTurn.id, response);
      if (!response.follow_up_question.trim()) {
        if (activeSession.current_round >= config.target_rounds) {
          await handleFinish(activeSession);
        } else {
          await handleNextQuestion(activeSession);
        }
      }
    } catch (answerError) {
      setError(getErrorMessage(answerError, t, "interview:errors.answer"));
    } finally {
      setStreamingDraft(null);
      setBusyAction(null);
    }
  }

  function applyMainFeedback(turnId: string, response: AnswerFeedback) {
    setTurns((current) =>
      updateTurn(current, turnId, (item) => ({
        ...item,
        feedback: response.feedback,
        missing_points: response.missing_points,
        follow_up_question: response.follow_up_question,
        weaknesses: response.weaknesses,
        review_suggestions: response.review_suggestions,
      })),
    );
  }

  async function handleFollowUp() {
    if (!session || !currentTurn || !composerValue.trim()) {
      return;
    }
    const activeSession = session;
    const submittedAnswer = composerValue.trim();
    setComposerValue("");
    setBusyAction("follow-up");
    setError(null);
    setStreamingDraft({
      kind: "follow-up-feedback",
      meta: t("follow_up_feedback"),
      text: "",
      turnId: currentTurn.id,
    });
    setTurns((current) =>
      updateTurn(current, currentTurn.id, (item) => ({
        ...item,
        follow_up_answer: submittedAnswer,
      })),
    );
    try {
      const response = await submitFollowUpAnswerStream(session.id, submittedAnswer, {
        onDelta: appendStreamingDelta,
      });
      applyFollowUpFeedback(currentTurn.id, response);
      if (activeSession.current_round < config.target_rounds) {
        await handleNextQuestion(activeSession);
      } else {
        await handleFinish(activeSession);
      }
    } catch (followUpError) {
      setError(getErrorMessage(followUpError, t, "interview:errors.follow_up"));
    } finally {
      setStreamingDraft(null);
      setBusyAction(null);
    }
  }

  function applyFollowUpFeedback(turnId: string, response: FollowUpFeedback) {
    setTurns((current) =>
      updateTurn(current, turnId, (item) => ({
        ...item,
        follow_up_feedback: response.feedback,
        follow_up_missing_points: response.missing_points,
        follow_up_weaknesses: response.weaknesses,
        follow_up_review_suggestions: response.review_suggestions,
      })),
    );
  }

  async function handleNextQuestion(activeSession = session) {
    if (!activeSession) {
      return;
    }
    setBusyAction("next");
    setError(null);
    setStreamingDraft({
      kind: "question",
      meta: t("question", { index: activeSession.current_round + 1 }),
      text: "",
    });
    try {
      const response = await nextQuestionStream(activeSession.id, {
        onDelta: appendStreamingDelta,
      });
      setSession(response.session);
      setTurns((current) => [...current, response.turn]);
      setComposerValue("");
    } catch (nextError) {
      setError(getErrorMessage(nextError, t, "interview:errors.next"));
    } finally {
      setStreamingDraft(null);
      setBusyAction(null);
    }
  }

  async function handleFinish(activeSession = session) {
    if (!activeSession) {
      return;
    }
    setBusyAction("finish");
    setError(null);
    setStreamingDraft({
      kind: "report",
      meta: t("report_generated"),
      text: "",
    });
    try {
      const response = await finishInterviewStream(activeSession.id, {
        onDelta: appendStreamingDelta,
      });
      setSession(response.session);
      setReport(response.report);
      notifyInterviewSessionsChanged();
    } catch (finishError) {
      setError(getErrorMessage(finishError, t, "interview:errors.finish"));
    } finally {
      setStreamingDraft(null);
      setBusyAction(null);
    }
  }

  function handleComposerSubmit() {
    if (composerMode === "start") {
      void handleStart();
    } else if (composerMode === "answer") {
      void handleAnswer();
    } else if (composerMode === "follow-up") {
      void handleFollowUp();
    }
  }

  const composerPlaceholder = (() => {
    if (composerMode === "answer") {
      return t("composer.answer_placeholder");
    }
    if (composerMode === "follow-up") {
      return t("composer.follow_up_placeholder");
    }
    if (composerMode === "start") {
      return t("composer.start_placeholder");
    }
    return t("composer.idle_placeholder");
  })();

  return (
    <div className="chat-workspace">
      <header className="chat-topbar">
        <span className="chat-topbar-title">
          {session
            ? t("round_summary", { current: session.current_round, total: config.target_rounds })
            : t("not_started")}
        </span>
        <span className="chat-topbar-model">{config.chat_model || t("model_unavailable")}</span>
      </header>

      <div className="chat-transcript" ref={transcriptRef}>
        {turns.length === 0 && !streamingDraft ? (
          <section className="chat-empty" aria-label={t("empty_label")}>
            <h2>{t("empty_title")}</h2>
            <p>{t("empty_body")}</p>
          </section>
        ) : (
          <div className="chat-thread">
            {openingContext ? (
              <ChatMessage tone="user" meta={t("context_message")}>
                <p>{openingContext}</p>
              </ChatMessage>
            ) : null}
            {turns.map((item) => (
              <div className="chat-turn" key={item.id}>
                <ChatMessage meta={t("question", { index: item.round_index })}>
                  <p>{item.question}</p>
                </ChatMessage>
                {item.answer ? (
                  <ChatMessage tone="user" meta={t("your_answer")}>
                    <p>{item.answer}</p>
                  </ChatMessage>
                ) : null}
                {streamingDraft?.turnId === item.id &&
                streamingDraft.kind === "feedback" &&
                !item.feedback &&
                streamingDraft.text ? (
                  <ChatMessage meta={streamingDraft.meta}>
                    <p>{streamingDraft.text}</p>
                  </ChatMessage>
                ) : null}
                {item.feedback ? (
                  <ChatMessage meta={t("feedback")}>
                    <p>{item.feedback}</p>
                    {item.missing_points.length > 0 ? (
                      <>
                        <h3>{t("missing_points")}</h3>
                        <ul>
                          {item.missing_points.map((point) => (
                            <li key={point}>{point}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {item.weaknesses.length > 0 ? (
                      <>
                        <h3>{t("weaknesses")}</h3>
                        <ul>
                          {item.weaknesses.map((weakness) => (
                            <li key={weakness}>{weakness}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {item.review_suggestions.length > 0 ? (
                      <>
                        <h3>{t("review_suggestions")}</h3>
                        <ul>
                          {item.review_suggestions.map((suggestion) => (
                            <li key={suggestion}>{suggestion}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                  </ChatMessage>
                ) : null}
                {item.follow_up_question ? (
                  <ChatMessage meta={t("follow_up")}>
                    <p>{item.follow_up_question}</p>
                  </ChatMessage>
                ) : null}
                {item.follow_up_answer ? (
                  <ChatMessage tone="user" meta={t("follow_up_answer")}>
                    <p>{item.follow_up_answer}</p>
                  </ChatMessage>
                ) : null}
                {streamingDraft?.turnId === item.id &&
                streamingDraft.kind === "follow-up-feedback" &&
                !item.follow_up_feedback &&
                streamingDraft.text ? (
                  <ChatMessage meta={streamingDraft.meta}>
                    <p>{streamingDraft.text}</p>
                  </ChatMessage>
                ) : null}
                {item.follow_up_feedback ? (
                  <ChatMessage meta={t("follow_up_feedback")}>
                    <p>{item.follow_up_feedback}</p>
                    {item.follow_up_missing_points.length > 0 ? (
                      <>
                        <h3>{t("missing_points")}</h3>
                        <ul>
                          {item.follow_up_missing_points.map((point) => (
                            <li key={point}>{point}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                  </ChatMessage>
                ) : null}
              </div>
            ))}
            {streamingDraft?.kind === "question" && !streamingDraft.turnId && streamingDraft.text ? (
              <ChatMessage meta={streamingDraft.meta}>
                <p>{streamingDraft.text}</p>
              </ChatMessage>
            ) : null}
            {streamingDraft?.kind === "report" && streamingDraft.text ? (
              <ChatMessage meta={streamingDraft.meta}>
                <p>{streamingDraft.text}</p>
              </ChatMessage>
            ) : null}
          </div>
        )}

        {busyAction && !streamingDraft?.text ? (
          <ChatMessage meta={t("streaming.label")} tone="assistant">
            <p className="typing-line">
              <Loader2 aria-hidden="true" size={16} />
              {busyAction === "answer"
                ? t("streaming.feedback")
                : busyAction === "follow-up"
                  ? t("streaming.follow_up")
                  : busyAction === "next"
                    ? t("streaming.question")
                    : busyAction === "finish"
                      ? t("finish_busy")
                      : t("streaming.start")}
            </p>
          </ChatMessage>
        ) : null}

        {error ? (
          <ChatMessage meta={t("error_title")} tone="system">
            <p className="form-error" role="alert">
              {error}
            </p>
          </ChatMessage>
        ) : null}

        {report ? (
          <ChatMessage meta={t("report_generated")} tone="assistant">
            <p>{report.summary}</p>
          </ChatMessage>
        ) : null}
      </div>

      <div className="chat-composer-wrap">
        <button
          aria-expanded={showAdvanced}
          className="button settings-toggle"
          onClick={() => setShowAdvanced((current) => !current)}
          type="button"
        >
          <Settings2 aria-hidden="true" size={17} />
          {t("interview_settings")}
        </button>

        {showAdvanced ? (
          <div className="advanced-settings">
            <LanguageSwitcher />
            <div className="form-grid">
              <label>
                <span className="field-label">{t("mode")}</span>
                <select
                  disabled={session?.status === "active"}
                  onChange={(event) => updateConfig("mode", event.target.value as InterviewMode)}
                  value={config.mode}
                >
                  {modeOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="field-label">{t("target_rounds")}</span>
                <input
                  disabled={session?.status === "active"}
                  max={12}
                  min={1}
                  onChange={(event) =>
                    updateConfig("target_rounds", Math.max(1, Number(event.target.value)))
                  }
                  type="number"
                  value={config.target_rounds}
                />
              </label>
            </div>
          </div>
        ) : null}

        <section className="chat-composer" aria-label={t("composer.label")}>
          <div className="composer-toolbar">
            {session?.status === "completed" ? (
              <span className="session-complete">
                <CheckCircle2 aria-hidden="true" size={16} />
                {t("completed")}
              </span>
            ) : null}
          </div>

          <div className="composer-box">
            <button className="icon-button" disabled type="button" aria-label={t("composer.attach")}>
              <Paperclip aria-hidden="true" size={18} />
            </button>
            <label className="sr-only" htmlFor="interview-composer">
              {t("composer.input_label")}
            </label>
            <textarea
              aria-label={t("composer.input_label")}
              disabled={composerMode === "idle"}
              id="interview-composer"
              onChange={(event) => setComposerValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  handleComposerSubmit();
                }
              }}
              placeholder={composerPlaceholder}
              rows={1}
              value={composerValue}
            />
            <div className="model-picker" data-open={modelMenuOpen}>
              <button
                aria-expanded={modelMenuOpen}
                aria-label={t("select_model")}
                className="model-picker-button"
                disabled={session?.status === "active" || providers.length === 0}
                onClick={() => setModelMenuOpen((current) => !current)}
                type="button"
              >
                <span>{config.chat_model || t("model_unavailable")}</span>
                <ChevronDown aria-hidden="true" size={14} />
              </button>
              {modelMenuOpen ? (
                <div className="model-picker-menu" role="listbox" aria-label={t("model")}>
                  {providers.length === 0 ? (
                    <span className="model-picker-empty">{t("no_providers")}</span>
                  ) : null}
                  {providers.map((provider) => (
                    <div className="model-picker-group" key={provider.provider}>
                      <p>{provider.provider}</p>
                      {provider.models.map((model) => {
                        const active =
                          provider.provider === config.chat_model_provider &&
                          model === config.chat_model;
                        return (
                          <button
                            data-active={active}
                            key={`${provider.provider}-${model}`}
                            onClick={() => {
                              updateConfig("chat_model_provider", provider.provider);
                              updateConfig("chat_model", model);
                              setModelMenuOpen(false);
                            }}
                            role="option"
                            aria-selected={active}
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
              aria-label={
                composerMode === "start"
                  ? t("composer.start_interview")
                  : composerMode === "follow-up"
                    ? t("composer.send_follow_up")
                    : t("composer.send_answer")
              }
              className="send-button"
              disabled={!canSend}
              onClick={handleComposerSubmit}
              type="button"
            >
              <Send aria-hidden="true" size={18} />
            </button>
          </div>

          <div className="composer-actions">
            {canRetryFinish ? (
              <button
                className="button"
                disabled={Boolean(busyAction)}
                onClick={() => {
                  void handleFinish(session);
                }}
                type="button"
              >
                <RotateCcw aria-hidden="true" size={17} />
                {t("retry_report")}
              </button>
            ) : null}
            <button
              className="button"
              disabled={!session || Boolean(busyAction)}
              onClick={() => {
                setSession(null);
                setTurns([]);
                setReport(null);
                setComposerValue("");
                setOpeningContext(null);
                setError(null);
                setStreamingDraft(null);
              }}
              type="button"
            >
              <RotateCcw aria-hidden="true" size={17} />
              {t("reset_session")}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
