"use client";

import { CheckCircle2, MessageSquareText, Play, SkipForward, Square } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import {
  createInterviewSession,
  finishInterview,
  getLastInterviewConfig,
  getModels,
  nextQuestion,
  saveLastInterviewConfig,
  submitAnswer,
  submitFollowUpAnswer,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type {
  AnswerFeedback,
  FollowUpFeedback,
  InterviewConfig,
  InterviewMode,
  InterviewSession,
  InterviewTurn,
  ModelProvider,
  ProviderName,
  ReportRecord,
} from "@/lib/types";

const defaultConfig: InterviewConfig = {
  target_company: "",
  target_role: "",
  job_description: "",
  extra_prompt: "",
  mode: "comprehensive",
  chat_model_provider: "qwen",
  chat_model: "qwen3.7-plus",
  target_rounds: 3,
};

export function InterviewWorkspace() {
  const { t } = useTranslation("interview");
  const [config, setConfig] = useState<InterviewConfig>(defaultConfig);
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [session, setSession] = useState<InterviewSession | null>(null);
  const [turn, setTurn] = useState<InterviewTurn | null>(null);
  const [feedback, setFeedback] = useState<AnswerFeedback | null>(null);
  const [followUpFeedback, setFollowUpFeedback] = useState<FollowUpFeedback | null>(null);
  const [report, setReport] = useState<ReportRecord | null>(null);
  const [answer, setAnswer] = useState("");
  const [followUpAnswer, setFollowUpAnswer] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const modeOptions: Array<{ value: InterviewMode; label: string }> = [
    { value: "comprehensive", label: t("mode_options.comprehensive") },
    { value: "project_deep_dive", label: t("mode_options.project_deep_dive") },
    { value: "knowledge_drill", label: t("mode_options.knowledge_drill") },
    { value: "weakness_reinforcement", label: t("mode_options.weakness_reinforcement") },
  ];

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getModels(), getLastInterviewConfig()]).then(([modelsResult, configResult]) => {
      if (cancelled) {
        return;
      }

      const availableProviders =
        modelsResult.status === "fulfilled" ? modelsResult.value.providers : [];

      if (modelsResult.status === "fulfilled") {
        setProviders(availableProviders);
      }
      if (configResult.status === "fulfilled") {
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
      } else if (availableProviders.length > 0) {
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
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedProvider = useMemo(
    () => providers.find((provider) => provider.provider === config.chat_model_provider),
    [config.chat_model_provider, providers],
  );
  const selectedModelAvailable =
    selectedProvider?.models.includes(config.chat_model) ?? false;
  const canStart =
    Boolean(config.target_company.trim()) &&
    Boolean(config.target_role.trim()) &&
    selectedModelAvailable &&
    !busyAction;

  function updateConfig<K extends keyof InterviewConfig>(field: K, value: InterviewConfig[K]) {
    setConfig((current) => ({ ...current, [field]: value }));
  }

  function selectProvider(providerName: ProviderName) {
    const provider = providers.find((item) => item.provider === providerName);
    setConfig((current) => ({
      ...current,
      chat_model_provider: providerName,
      chat_model: provider?.models[0] ?? "",
    }));
  }

  async function handleStart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canStart) {
      return;
    }

    setBusyAction("start");
    setError(null);
    setReport(null);
    try {
      await saveLastInterviewConfig(config);
      const created = await createInterviewSession(config);
      setSession(created.session);
      setTurn(created.turn);
      setFeedback(null);
      setFollowUpFeedback(null);
      setAnswer("");
      setFollowUpAnswer("");
    } catch (startError) {
      setError(getErrorMessage(startError, t, "interview:errors.start"));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleAnswer() {
    if (!session || !turn || !answer.trim()) {
      return;
    }
    setBusyAction("answer");
    setError(null);
    try {
      const response = await submitAnswer(session.id, answer.trim());
      setFeedback(response);
      setFollowUpFeedback(null);
      setTurn({
        ...turn,
        answer: answer.trim(),
        feedback: response.feedback,
        missing_points: response.missing_points,
        follow_up_question: response.follow_up_question,
        weaknesses: response.weaknesses,
        review_suggestions: response.review_suggestions,
      });
    } catch (answerError) {
      setError(getErrorMessage(answerError, t, "interview:errors.answer"));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleFollowUp() {
    if (!session || !turn || !followUpAnswer.trim()) {
      return;
    }
    setBusyAction("follow-up");
    setError(null);
    try {
      const response = await submitFollowUpAnswer(session.id, followUpAnswer.trim());
      setFollowUpFeedback(response);
      setTurn({
        ...turn,
        follow_up_answer: followUpAnswer.trim(),
        follow_up_feedback: response.feedback,
        follow_up_missing_points: response.missing_points,
        follow_up_weaknesses: response.weaknesses,
        follow_up_review_suggestions: response.review_suggestions,
      });
    } catch (followUpError) {
      setError(getErrorMessage(followUpError, t, "interview:errors.follow_up"));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleNextQuestion() {
    if (!session) {
      return;
    }
    setBusyAction("next");
    setError(null);
    try {
      const response = await nextQuestion(session.id);
      setSession(response.session);
      setTurn(response.turn);
      setFeedback(null);
      setFollowUpFeedback(null);
      setAnswer("");
      setFollowUpAnswer("");
    } catch (nextError) {
      setError(getErrorMessage(nextError, t, "interview:errors.next"));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleFinish() {
    if (!session) {
      return;
    }
    setBusyAction("finish");
    setError(null);
    try {
      const response = await finishInterview(session.id);
      setSession(response.session);
      setReport(response.report);
    } catch (finishError) {
      setError(getErrorMessage(finishError, t, "interview:errors.finish"));
    } finally {
      setBusyAction(null);
    }
  }

  const reachedTargetRounds =
    session !== null && session.current_round >= config.target_rounds;
  const statusHint = (() => {
    if (!turn?.answer) {
      return null;
    }
    if (reachedTargetRounds) {
      return t("guidance.final_round");
    }
    if (turn.follow_up_answer) {
      return t("guidance.follow_up_complete");
    }
    return t("guidance.continue");
  })();

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("eyebrow")}</p>
          <h1>{t("title")}</h1>
        </div>
        <p className="page-summary">
          {session
            ? t("round_summary", { current: session.current_round, total: config.target_rounds })
            : t("not_started")}
        </p>
      </header>

      <div className="interview-layout">
        <form className="tool-panel interview-config" onSubmit={handleStart}>
          <div className="section-heading">
            <div>
              <p className="eyebrow">{t("setup_eyebrow")}</p>
              <h2>{t("setup_title")}</h2>
            </div>
          </div>

          <label>
            <span className="field-label">{t("target_company")}</span>
            <input
              disabled={session?.status === "active"}
              onChange={(event) => updateConfig("target_company", event.target.value)}
              required
              value={config.target_company}
            />
          </label>
          <label>
            <span className="field-label">{t("target_role")}</span>
            <input
              disabled={session?.status === "active"}
              onChange={(event) => updateConfig("target_role", event.target.value)}
              required
              value={config.target_role}
            />
          </label>
          <label>
            <span className="field-label">{t("job_description")}</span>
            <textarea
              disabled={session?.status === "active"}
              onChange={(event) => updateConfig("job_description", event.target.value)}
              rows={5}
              value={config.job_description}
            />
          </label>
          <label>
            <span className="field-label">{t("extra_prompt")}</span>
            <textarea
              disabled={session?.status === "active"}
              onChange={(event) => updateConfig("extra_prompt", event.target.value)}
              rows={3}
              value={config.extra_prompt}
            />
          </label>
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
          <div className="form-grid">
            <label>
              <span className="field-label">{t("provider")}</span>
              <select
                disabled={session?.status === "active" || providers.length === 0}
                onChange={(event) => selectProvider(event.target.value as ProviderName)}
                value={selectedProvider ? config.chat_model_provider : ""}
              >
                {providers.length === 0 ? <option value="">{t("no_providers")}</option> : null}
                {providers.map((provider) => (
                  <option key={provider.provider} value={provider.provider}>
                    {provider.provider}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span className="field-label">{t("model")}</span>
              <select
                disabled={session?.status === "active" || !selectedProvider}
                onChange={(event) => updateConfig("chat_model", event.target.value)}
                value={selectedModelAvailable ? config.chat_model : ""}
              >
                {!selectedProvider ? <option value="">{t("model_unavailable")}</option> : null}
                {selectedProvider?.models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>
          </div>
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

          <button
            className="button button-primary"
            disabled={!canStart || session?.status === "active"}
            type="submit"
          >
            <Play aria-hidden="true" size={17} />
            {busyAction === "start" ? `${t("common:actions.start")}...` : t("common:actions.start")}
          </button>
        </form>

        <section className="session-panel" aria-labelledby="session-heading">
          <div className="section-heading">
            <div>
              <p className="eyebrow">{t("session_eyebrow")}</p>
              <h2 id="session-heading">{t("session_title")}</h2>
            </div>
            {session?.status === "completed" ? (
              <span className="session-complete">
                <CheckCircle2 aria-hidden="true" size={17} />
                {t("completed")}
              </span>
            ) : null}
          </div>

          {!turn ? (
            <div className="empty-session">
              <MessageSquareText aria-hidden="true" size={24} />
              <p>{t("session_empty")}</p>
            </div>
          ) : (
            <div className="session-content">
              <div className="question-block">
                <p className="eyebrow">{t("question", { index: turn.round_index })}</p>
                <h3>{turn.question}</h3>
              </div>

              <label>
                <span className="field-label">{t("your_answer")}</span>
                <textarea
                  disabled={Boolean(turn.answer) || session?.status !== "active"}
                  onChange={(event) => setAnswer(event.target.value)}
                  rows={7}
                  value={answer}
                />
              </label>
              <button
                className="button button-primary"
                disabled={
                  !answer.trim() ||
                  Boolean(turn.answer) ||
                  Boolean(busyAction) ||
                  session?.status !== "active"
                }
                onClick={() => void handleAnswer()}
                type="button"
              >
                {busyAction === "answer" ? t("submit_answer_busy") : t("submit_answer")}
              </button>

              {feedback ? (
                <div className="feedback-panel">
                  <div>
                    <p className="eyebrow">{t("feedback")}</p>
                    <p>{feedback.feedback}</p>
                  </div>
                  {feedback.missing_points.length > 0 ? (
                    <div>
                      <h4>{t("missing_points")}</h4>
                      <ul>
                        {feedback.missing_points.map((point) => (
                          <li key={point}>{point}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <div>
                    <p className="eyebrow">{t("follow_up")}</p>
                    <p>{feedback.follow_up_question}</p>
                  </div>
                  <label>
                    <span className="field-label">{t("follow_up_answer")}</span>
                    <textarea
                      disabled={Boolean(turn.follow_up_answer) || session?.status !== "active"}
                      onChange={(event) => setFollowUpAnswer(event.target.value)}
                      rows={5}
                      value={followUpAnswer}
                    />
                  </label>
                  <button
                    className="button"
                    disabled={
                      !followUpAnswer.trim() ||
                      Boolean(turn.follow_up_answer) ||
                      Boolean(busyAction) ||
                      session?.status !== "active"
                    }
                    onClick={() => void handleFollowUp()}
                    type="button"
                  >
                    {busyAction === "follow-up" ? t("submit_follow_up_busy") : t("submit_follow_up")}
                  </button>
                  {followUpFeedback ? (
                    <div className="follow-up-feedback-panel">
                      <div>
                        <p className="eyebrow">{t("follow_up_feedback")}</p>
                        <p>{followUpFeedback.feedback}</p>
                      </div>
                      {followUpFeedback.missing_points.length > 0 ? (
                        <div>
                          <h4>{t("missing_points")}</h4>
                          <ul>
                            {followUpFeedback.missing_points.map((point) => (
                              <li key={point}>{point}</li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              ) : null}

              {statusHint ? <p className="session-guidance">{statusHint}</p> : null}

              <div className="button-row session-actions">
                <button
                  className="button"
                  disabled={
                    !turn.answer ||
                    Boolean(busyAction) ||
                    reachedTargetRounds ||
                    session?.status !== "active"
                  }
                  onClick={() => void handleNextQuestion()}
                  type="button"
                >
                  <SkipForward aria-hidden="true" size={17} />
                  {busyAction === "next" ? t("next_question_busy") : t("next_question")}
                </button>
                <button
                  className="button button-danger"
                  disabled={Boolean(busyAction) || session?.status !== "active"}
                  onClick={() => void handleFinish()}
                  type="button"
                >
                  <Square aria-hidden="true" size={15} />
                  {busyAction === "finish" ? t("finish_busy") : t("common:actions.finish")}
                </button>
              </div>
            </div>
          )}

          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
          {report ? (
            <div className="completion-panel">
              <CheckCircle2 aria-hidden="true" size={20} />
              <div>
                <strong>{t("report_generated")}</strong>
                <p>{report.summary}</p>
              </div>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
