"use client";

import { CheckCircle2, MessageSquareText, Play, SkipForward, Square } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

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
import type {
  AnswerFeedback,
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
  chat_model_provider: "openai",
  chat_model: "gpt-4.1-mini",
  target_rounds: 3,
};

const modeOptions: Array<{ value: InterviewMode; label: string }> = [
  { value: "comprehensive", label: "Comprehensive" },
  { value: "project_deep_dive", label: "Project deep dive" },
  { value: "knowledge_drill", label: "Knowledge drill" },
  { value: "weakness_reinforcement", label: "Weakness reinforcement" },
];

export function InterviewWorkspace() {
  const [config, setConfig] = useState<InterviewConfig>(defaultConfig);
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [session, setSession] = useState<InterviewSession | null>(null);
  const [turn, setTurn] = useState<InterviewTurn | null>(null);
  const [feedback, setFeedback] = useState<AnswerFeedback | null>(null);
  const [report, setReport] = useState<ReportRecord | null>(null);
  const [answer, setAnswer] = useState("");
  const [followUpAnswer, setFollowUpAnswer] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getModels(), getLastInterviewConfig()]).then(([modelsResult, configResult]) => {
      if (cancelled) {
        return;
      }

      if (modelsResult.status === "fulfilled") {
        setProviders(modelsResult.value.providers);
      }
      if (configResult.status === "fulfilled") {
        const { id: _id, is_last_used: _isLastUsed, updated_at: _updatedAt, ...lastConfig } =
          configResult.value;
        setConfig(lastConfig);
      }
      if (modelsResult.status === "rejected" || configResult.status === "rejected") {
        setError("Could not load the saved configuration or model availability.");
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
      setAnswer("");
      setFollowUpAnswer("");
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : "Failed to start interview.");
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
      setError(answerError instanceof Error ? answerError.message : "Failed to submit answer.");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleFollowUp() {
    if (!session || !followUpAnswer.trim()) {
      return;
    }
    setBusyAction("follow-up");
    setError(null);
    try {
      const response = await submitFollowUpAnswer(session.id, followUpAnswer.trim());
      setTurn(response);
    } catch (followUpError) {
      setError(
        followUpError instanceof Error ? followUpError.message : "Failed to submit follow-up.",
      );
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
      setAnswer("");
      setFollowUpAnswer("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load next question.");
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
      setError(finishError instanceof Error ? finishError.message : "Failed to finish interview.");
    } finally {
      setBusyAction(null);
    }
  }

  const reachedTargetRounds =
    session !== null && session.current_round >= config.target_rounds;

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Mock interview</p>
          <h1>Interview</h1>
        </div>
        <p className="page-summary">
          {session ? `Round ${session.current_round} of ${config.target_rounds}` : "Not started"}
        </p>
      </header>

      <div className="interview-layout">
        <form className="tool-panel interview-config" onSubmit={handleStart}>
          <div className="section-heading">
            <div>
              <p className="eyebrow">Setup</p>
              <h2>Configuration</h2>
            </div>
          </div>

          <label>
            <span className="field-label">Target company</span>
            <input
              disabled={session?.status === "active"}
              onChange={(event) => updateConfig("target_company", event.target.value)}
              required
              value={config.target_company}
            />
          </label>
          <label>
            <span className="field-label">Target role</span>
            <input
              disabled={session?.status === "active"}
              onChange={(event) => updateConfig("target_role", event.target.value)}
              required
              value={config.target_role}
            />
          </label>
          <label>
            <span className="field-label">Job description</span>
            <textarea
              disabled={session?.status === "active"}
              onChange={(event) => updateConfig("job_description", event.target.value)}
              rows={5}
              value={config.job_description}
            />
          </label>
          <label>
            <span className="field-label">Extra prompt</span>
            <textarea
              disabled={session?.status === "active"}
              onChange={(event) => updateConfig("extra_prompt", event.target.value)}
              rows={3}
              value={config.extra_prompt}
            />
          </label>
          <label>
            <span className="field-label">Mode</span>
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
              <span className="field-label">Provider</span>
              <select
                disabled={session?.status === "active" || providers.length === 0}
                onChange={(event) => selectProvider(event.target.value as ProviderName)}
                value={selectedProvider ? config.chat_model_provider : ""}
              >
                {providers.length === 0 ? <option value="">No providers configured</option> : null}
                {providers.map((provider) => (
                  <option key={provider.provider} value={provider.provider}>
                    {provider.provider}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span className="field-label">Model</span>
              <select
                disabled={session?.status === "active" || !selectedProvider}
                onChange={(event) => updateConfig("chat_model", event.target.value)}
                value={selectedModelAvailable ? config.chat_model : ""}
              >
                {!selectedProvider ? <option value="">Unavailable</option> : null}
                {selectedProvider?.models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label>
            <span className="field-label">Target rounds</span>
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
            {busyAction === "start" ? "Starting..." : "Start interview"}
          </button>
        </form>

        <section className="session-panel" aria-labelledby="session-heading">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Live session</p>
              <h2 id="session-heading">Question and feedback</h2>
            </div>
            {session?.status === "completed" ? (
              <span className="session-complete">
                <CheckCircle2 aria-hidden="true" size={17} />
                Completed
              </span>
            ) : null}
          </div>

          {!turn ? (
            <div className="empty-session">
              <MessageSquareText aria-hidden="true" size={24} />
              <p>Configure an available model and start an interview.</p>
            </div>
          ) : (
            <div className="session-content">
              <div className="question-block">
                <p className="eyebrow">Question {turn.round_index}</p>
                <h3>{turn.question}</h3>
              </div>

              <label>
                <span className="field-label">Your answer</span>
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
                Submit answer
              </button>

              {feedback ? (
                <div className="feedback-panel">
                  <div>
                    <p className="eyebrow">Feedback</p>
                    <p>{feedback.feedback}</p>
                  </div>
                  {feedback.missing_points.length > 0 ? (
                    <div>
                      <h4>Missing points</h4>
                      <ul>
                        {feedback.missing_points.map((point) => (
                          <li key={point}>{point}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <div>
                    <p className="eyebrow">Follow-up</p>
                    <p>{feedback.follow_up_question}</p>
                  </div>
                  <label>
                    <span className="field-label">Follow-up answer</span>
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
                    Submit follow-up
                  </button>
                </div>
              ) : null}

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
                  Next question
                </button>
                <button
                  className="button button-danger"
                  disabled={Boolean(busyAction) || session?.status !== "active"}
                  onClick={() => void handleFinish()}
                  type="button"
                >
                  <Square aria-hidden="true" size={15} />
                  Finish
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
                <strong>Report generated</strong>
                <p>{report.summary}</p>
              </div>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
