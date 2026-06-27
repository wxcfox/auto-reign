"use client";

import {
  Loader2,
  Paperclip,
  Send,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { ChatMessage } from "@/components/ChatMessage";
import { ModelPicker } from "@/components/ModelPicker";
import { useTranslation } from "@/hooks/useTranslation";
import {
  createInterviewSessionStream,
  finishInterviewSessionStream,
  getInterviewSession,
  getLastInterviewConfig,
  getModels,
  getReports,
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
  InterviewSession,
  InterviewTurn,
  ModelProvider,
  ProviderName,
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
  target_rounds: 1,
};

type ComposerMode = "start" | "answer" | "follow-up" | "next" | "idle";

type StreamingDraft = {
  kind: "question" | "feedback" | "follow-up-feedback" | "summary";
  meta: string;
  text: string;
  turnId?: string;
};

function updateTurn(
  turns: InterviewTurn[],
  turnId: string,
  updater: (turn: InterviewTurn) => InterviewTurn,
) {
  return turns.map((item) => (item.id === turnId ? updater(item) : item));
}

function writeSuggestions(turn: InterviewTurn) {
  return [
    turn.should_write_weakness ? "weakness" : null,
    turn.should_write_high_frequency ? "high_frequency" : null,
  ].filter((item): item is "weakness" | "high_frequency" => item !== null);
}

function inferRequestedRounds(text: string): number | null {
  const match = text.match(/(\d{1,2})\s*(轮|题|道|round|rounds|questions?)/i);
  if (match) {
    return Math.max(1, Math.min(12, Number(match[1])));
  }
  const chineseDigits: Record<string, number> = {
    一: 1,
    两: 2,
    二: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
  };
  for (const [digit, value] of Object.entries(chineseDigits)) {
    if (new RegExp(`${digit}\\s*(轮|题|道)`).test(text)) {
      return value;
    }
  }
  if (/考考我|抽检|quiz me/i.test(text)) {
    return 5;
  }
  return null;
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
  const [composerValue, setComposerValue] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streamingDraft, setStreamingDraft] = useState<StreamingDraft | null>(null);
  const [sessionSummary, setSessionSummary] = useState<string | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [openingContext, setOpeningContext] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);

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
          const nextConfig = {
            ...lastConfig,
            target_company: "",
            target_role: "",
            job_description: "",
            extra_prompt: "",
            mode: "comprehensive" as const,
            target_rounds: 1,
          };
          const matchedProvider = availableProviders.find(
            (provider) => provider.provider === nextConfig.chat_model_provider,
          );
          if (matchedProvider?.models.includes(nextConfig.chat_model)) {
            setConfig(nextConfig);
          } else if (availableProviders.length > 0) {
            setConfig((current) => ({
              ...current,
              ...nextConfig,
              chat_model_provider: availableProviders[0].provider,
              chat_model: availableProviders[0].models[0] ?? "",
            }));
          } else {
            setConfig(nextConfig);
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
        setOpeningContext(loadedConfig.extra_prompt.trim() || null);
        setComposerValue("");
        setStreamingDraft(null);
        setSessionSummary(null);
        if (detail.session.status === "completed" && detail.session.report_path) {
          getReports()
            .then((reportList) => {
              if (cancelled) {
                return;
              }
              const report = reportList.reports.find(
                (item) => item.session_id === detail.session.id,
              );
              setSessionSummary(report?.summary ?? null);
            })
            .catch(() => {
              if (!cancelled) {
                setSessionSummary(null);
              }
            });
        }
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
    return "next";
  })();
  const canSend =
    !busyAction &&
    ((composerMode === "start" && canStart) ||
      (composerMode === "next" && session?.status === "active") ||
      (session?.status === "active" &&
        Boolean(composerValue.trim()) &&
        (composerMode === "answer" || composerMode === "follow-up")));

  function selectModel(provider: ProviderName, model: string) {
    setConfig((current) => ({
      ...current,
      chat_model_provider: provider,
      chat_model: model,
    }));
    setModelMenuOpen(false);
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
    const startContext = composerValue.trim();
    setOpeningContext(startContext || null);
    setSessionSummary(null);
    setStreamingDraft({ kind: "question", meta: t("question", { index: 1 }), text: "" });
    try {
      const activeConfig: InterviewConfig = {
        ...config,
        target_company: "",
        target_role: "",
        job_description: "",
        extra_prompt: startContext,
        language: getCurrentLanguage() === "zh-CN" ? "zh-CN" : "en",
        target_rounds: inferRequestedRounds(startContext) ?? 1,
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
        if (activeSession.current_round < config.target_rounds) {
          await handleNextQuestion(activeSession);
        } else {
          await handleFinishSession(activeSession);
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
        better_answer: response.better_answer,
        mastery_change: response.mastery_change,
        should_write_weakness: response.should_write_weakness,
        should_write_high_frequency: response.should_write_high_frequency,
        tested_points: response.tested_points,
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
        await handleFinishSession(activeSession);
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
        better_answer: response.better_answer,
        mastery_change: response.mastery_change,
        should_write_weakness: response.should_write_weakness,
        should_write_high_frequency: response.should_write_high_frequency,
        tested_points: response.tested_points,
      })),
    );
  }

  async function handleNextQuestion(activeSession = session, intent = "") {
    if (!activeSession) {
      return;
    }
    const nextIntent = intent.trim();
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
      }, nextIntent);
      setSession(response.session);
      setTurns((current) => [...current, response.turn]);
      if (nextIntent) {
        setConfig((current) => ({
          ...current,
          extra_prompt: [current.extra_prompt, nextIntent].filter(Boolean).join("\n"),
          target_rounds: current.target_rounds + (inferRequestedRounds(nextIntent) ?? 1),
        }));
      }
      setComposerValue("");
    } catch (nextError) {
      setError(getErrorMessage(nextError, t, "interview:errors.next"));
    } finally {
      setStreamingDraft(null);
      setBusyAction(null);
    }
  }

  async function handleFinishSession(activeSession = session) {
    if (!activeSession || activeSession.status !== "active") {
      return;
    }
    setBusyAction("finish");
    setError(null);
    setStreamingDraft({
      kind: "summary",
      meta: t("overall_summary"),
      text: "",
    });
    try {
      const response = await finishInterviewSessionStream(activeSession.id, {
        onDelta: appendStreamingDelta,
      });
      setSession(response.session);
      setSessionSummary(response.report.summary);
      setComposerValue("");
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
    } else if (composerMode === "next") {
      const nextIntent = composerValue.trim();
      setComposerValue("");
      void handleNextQuestion(session, nextIntent);
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
    if (composerMode === "next") {
      return t("composer.next_placeholder");
    }
    return t("composer.idle_placeholder");
  })();

  return (
    <div className="chat-workspace interview-workspace">
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
                    {item.better_answer ? (
                      <>
                        <h3>{t("better_answer")}</h3>
                        <p>{item.better_answer}</p>
                      </>
                    ) : null}
                    {item.tested_points && item.tested_points.length > 0 ? (
                      <>
                        <h3>{t("tested_points")}</h3>
                        <ul>
                          {item.tested_points.map((point) => (
                            <li key={point}>{point}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {item.mastery_change && item.mastery_change !== "unchanged" ? (
                      <>
                        <h3>{t("mastery_change")}</h3>
                        <p>{item.mastery_change}</p>
                      </>
                    ) : null}
                    {writeSuggestions(item).length > 0 ? (
                      <>
                        <h3>{t("write_suggestions")}</h3>
                        <ul>
                          {writeSuggestions(item).map((suggestion) => (
                            <li key={suggestion}>
                              {suggestion === "weakness"
                                ? t("write_weakness")
                                : t("write_high_frequency")}
                            </li>
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
            {sessionSummary ? (
              <ChatMessage meta={t("overall_summary")}>
                <p>{sessionSummary}</p>
              </ChatMessage>
            ) : null}
            {!sessionSummary &&
            streamingDraft?.kind === "summary" &&
            streamingDraft.text ? (
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
                  : busyAction === "finish"
                    ? t("streaming.summary")
                  : busyAction === "next"
                    ? t("streaming.question")
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

      </div>

      <div className="chat-composer-wrap">
        <section className="chat-composer" aria-label={t("composer.label")}>
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
            <ModelPicker
              disabled={session?.status === "active"}
              labels={{
                listbox: t("model"),
                modelUnavailable: t("model_unavailable"),
                noProviders: t("no_providers"),
                selectModel: t("select_model"),
              }}
              onOpenChange={setModelMenuOpen}
              onSelect={selectModel}
              open={modelMenuOpen}
              providers={providers}
              selectedModel={config.chat_model}
              selectedProvider={config.chat_model_provider}
            />
            <button
              aria-label={
                composerMode === "start"
                  ? t("composer.start_interview")
                  : composerMode === "follow-up"
                    ? t("composer.send_follow_up")
                    : composerMode === "next"
                      ? t("composer.next_question")
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
        </section>
      </div>
    </div>
  );
}
