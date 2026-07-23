"use client";

import { Loader2, RotateCcw, Send, Square } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { AgentPicker } from "@/components/AgentPicker";
import { AttachmentPicker } from "@/components/AttachmentPicker";
import { AutoResizeTextarea } from "@/components/AutoResizeTextarea";
import { ChatMessage } from "@/components/ChatMessage";
import { ModelPicker } from "@/components/ModelPicker";
import { SubtaskContexts } from "@/components/SubtaskContexts";
import { useTaskChat } from "@/components/chat/useTaskChat";
import { useTranslation } from "@/hooks/useTranslation";
import {
  getModels,
  getTask,
  listAgents,
  listSubtaskContextDrafts,
  setTaskModel,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { getErrorMessage } from "@/lib/error-messages";
import { MAX_ATTACHMENTS_PER_MESSAGE } from "@/lib/limits";
import { notifyTasksChanged } from "@/lib/task-events";
import type {
  Agent,
  ModelProvider,
  ModelRef,
  SubtaskContextBrief,
  TaskAgentResponse,
  TaskDetailResponse,
} from "@/lib/types";

type ChatWorkspaceProps = {
  taskId?: number;
};

function sameModel(left: ModelRef | null, right: ModelRef | null) {
  return left?.provider === right?.provider && left?.model === right?.model;
}

export function ChatWorkspace({ taskId }: ChatWorkspaceProps = {}) {
  const router = useRouter();
  const { t } = useTranslation("chat");
  const requestedTaskId = taskId ?? null;
  const chat = useTaskChat(requestedTaskId);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [platformDefault, setPlatformDefault] = useState<ModelRef | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [selectedModelOverride, setSelectedModelOverride] = useState<ModelRef | null>(null);
  const [taskDetail, setTaskDetail] = useState<TaskDetailResponse | null>(null);
  const [composerValue, setComposerValue] = useState("");
  const [draftContexts, setDraftContexts] = useState<SubtaskContextBrief[]>([]);
  const [contextLoading, setContextLoading] = useState(true);
  const [contextRecoveryError, setContextRecoveryError] = useState<string | null>(null);
  const [contextMutationPending, setContextMutationPending] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [modelUpdating, setModelUpdating] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef(0);
  const modelOperationRef = useRef<symbol | null>(null);
  const boundContextIdsRef = useRef(new Set<number>());

  useEffect(() => {
    const view = ++viewRef.current;
    setAgents([]);
    setProviders([]);
    setPlatformDefault(null);
    setSelectedAgentId(null);
    setSelectedModelOverride(null);
    setTaskDetail(null);
    setComposerValue("");
    setOptionsLoading(true);
    setLoadFailed(false);
    setModelUpdating(false);
    modelOperationRef.current = null;
    setLocalError(null);
    setModelMenuOpen(false);

    const load = async () => {
      const agentsRequest = listAgents("visible");
      const modelsRequest = getModels();
      const taskRequest = requestedTaskId === null ? null : getTask(requestedTaskId);
      const [agentResult, modelResult, detailResult] = await Promise.allSettled([
        agentsRequest,
        modelsRequest,
        taskRequest ?? Promise.resolve(null),
      ]);
      if (viewRef.current !== view) return;
      if (agentResult.status === "fulfilled") setAgents(agentResult.value.agents);
      if (modelResult.status === "fulfilled") {
        setProviders(modelResult.value.providers);
        setPlatformDefault(modelResult.value.default);
      }
      if (detailResult.status === "fulfilled" && detailResult.value) {
        setTaskDetail(detailResult.value);
        setSelectedAgentId(detailResult.value.agent.id);
        setSelectedModelOverride(detailResult.value.model_override);
      }
      const detailFailed = requestedTaskId !== null && detailResult.status === "rejected";
      if (detailFailed) setLoadFailed(true);
      if (
        agentResult.status === "rejected" ||
        modelResult.status === "rejected" ||
        detailFailed
      ) {
        setLocalError(
          detailFailed
            ? t("errors.load")
            : t("errors.options_load"),
        );
      }
      setOptionsLoading(false);
    };
    void load();
    return () => {
      if (viewRef.current === view) viewRef.current += 1;
    };
  }, [requestedTaskId, t]);

  const recoverDrafts = async (view: number) => {
    setContextLoading(true);
    setContextRecoveryError(null);
    try {
      const drafts = await listSubtaskContextDrafts();
      if (viewRef.current === view) {
        setDraftContexts(
          drafts.filter((context) => !boundContextIdsRef.current.has(context.id)),
        );
      }
    } catch {
      if (viewRef.current === view) {
        setContextRecoveryError(t("contexts.loadFailed"));
      }
    } finally {
      if (viewRef.current === view) setContextLoading(false);
    }
  };

  useEffect(() => {
    const view = viewRef.current;
    setDraftContexts([]);
    void recoverDrafts(view);
    // Draft Contexts are user-scoped (subtask_id=0), but refreshing on a Task
    // route change prevents a stale composer snapshot crossing views.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedTaskId]);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!transcript) return;
    transcript.scrollTo?.({ top: transcript.scrollHeight, behavior: "smooth" });
    transcript.scrollTop = transcript.scrollHeight;
  }, [chat.messages, chat.sending]);

  const lockedAgent: TaskAgentResponse | null = taskDetail?.agent ?? null;
  const selectedAgentDefinition = useMemo(
    () => agents.find((agent) => agent.id === (lockedAgent?.id ?? selectedAgentId)) ?? null,
    [agents, lockedAgent?.id, selectedAgentId],
  );
  const agentDefault = selectedAgentDefinition?.config.default_model ?? platformDefault;
  const resolvedModel = selectedModelOverride ?? agentDefault;
  const activeAssistant = chat.messages.find(
    (message) =>
      message.role === "ASSISTANT" &&
      (message.status === "PENDING" || message.status === "RUNNING"),
  );
  const effectiveTaskStatus = chat.taskStatus ?? taskDetail?.status ?? null;
  const taskRunning =
    chat.sending ||
    activeAssistant !== undefined ||
    effectiveTaskStatus === "PENDING" ||
    effectiveTaskStatus === "RUNNING";
  const disconnected = !chat.connected;
  const agentUnavailable = lockedAgent !== null && !lockedAgent.is_available;
  const loading = optionsLoading || chat.loading;
  const interactionDisabled =
    loading ||
    loadFailed ||
    disconnected ||
    agentUnavailable ||
    taskRunning ||
    modelUpdating ||
    contextLoading ||
    contextRecoveryError !== null ||
    contextMutationPending;
  const canSend =
    composerValue.trim().length > 0 &&
    resolvedModel !== null &&
    draftContexts.length <= MAX_ATTACHMENTS_PER_MESSAGE &&
    draftContexts.every((context) => context.status === "ready") &&
    !interactionDisabled;

  async function selectModel(value: ModelRef | null) {
    if (
      interactionDisabled ||
      modelOperationRef.current !== null ||
      sameModel(value, selectedModelOverride)
    ) return;
    if (requestedTaskId === null) {
      setSelectedModelOverride(value);
      return;
    }
    const view = viewRef.current;
    const operation = Symbol("task-model-update");
    const operationTaskId = requestedTaskId;
    modelOperationRef.current = operation;
    setModelUpdating(true);
    setLocalError(null);
    try {
      const detail = await setTaskModel(operationTaskId, value);
      notifyTasksChanged(detail);
      if (viewRef.current !== view || modelOperationRef.current !== operation) return;
      setTaskDetail(detail);
      setSelectedModelOverride(detail.model_override);
    } catch (cause) {
      if (viewRef.current !== view || modelOperationRef.current !== operation) return;
      setLocalError(
        cause instanceof ApiError
          ? getErrorMessage(cause, t, "chat:errors.model_update")
          : t("errors.model_update"),
      );
    } finally {
      if (viewRef.current === view && modelOperationRef.current === operation) {
        modelOperationRef.current = null;
        setModelUpdating(false);
      }
    }
  }

  async function handleSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const message = composerValue.trim();
    if (!canSend || !message) return;
    const view = viewRef.current;
    const submittedContexts = [...draftContexts];
    setComposerValue("");
    setLocalError(null);
    chat.clearError();
    try {
      const ack = await chat.send(message, {
        agentId: requestedTaskId === null ? selectedAgentId : undefined,
        modelOverride: requestedTaskId === null ? selectedModelOverride : undefined,
        contextIds: submittedContexts.map((context) => context.id),
        contexts: submittedContexts,
      });
      const boundIds = new Set(submittedContexts.map((context) => context.id));
      for (const contextId of boundIds) boundContextIdsRef.current.add(contextId);
      setDraftContexts((current) => current.filter((context) => !boundIds.has(context.id)));
      notifyTasksChanged();
      if (viewRef.current === view && requestedTaskId === null) {
        router.replace(`/chat?task=${ack.task_id}`, { scroll: false });
      }
    } catch {
      if (viewRef.current === view) {
        setComposerValue((current) => current || message);
      }
    }
  }

  async function handleCancel() {
    setLocalError(null);
    try {
      await chat.cancelTask();
    } catch {
      // useTaskChat exposes only the stable error code below.
    }
  }

  async function handleRetry(subtaskId: number) {
    setLocalError(null);
    try {
      await chat.retryAssistant(subtaskId);
      notifyTasksChanged();
    } catch {
      // useTaskChat exposes only the stable error code below.
    }
  }

  const socketError = chat.errorCode
    ? t(`errors.${chat.errorCode}`, { defaultValue: t("errors.send") })
    : null;

  return (
    <div className="chat-workspace general-chat-workspace">
      <header className="chat-topbar">
        <span className="chat-topbar-title">{taskDetail?.name ?? t("title")}</span>
        <span className="chat-topbar-model">
          {resolvedModel
            ? `${resolvedModel.provider} / ${resolvedModel.model}`
            : t("modelPicker.modelUnavailable")}
        </span>
      </header>

      <div className="chat-transcript" ref={transcriptRef}>
        {chat.messages.length === 0 ? (
          <section aria-label={t("empty_label")} className="chat-empty">
            <h2>{loading ? t("loading") : t("empty_title")}</h2>
          </section>
        ) : (
          <div className="chat-thread">
            {chat.messages.map((message) => {
              const failed = message.role === "ASSISTANT" && message.status === "FAILED";
              return (
                <ChatMessage
                  blocks={message.role === "ASSISTANT" && message.blocks.length > 0
                    ? message.blocks
                    : undefined}
                  failed={failed}
                  failedLabel={failed ? t("failed_response") : undefined}
                  footer={failed && message.subtaskId !== null ? (
                    <button
                      disabled={taskRunning || chat.retryingSubtaskId !== null || disconnected}
                      onClick={() => void handleRetry(message.subtaskId!)}
                      type="button"
                    >
                      <RotateCcw aria-hidden="true" size={14} />
                      {chat.retryingSubtaskId === message.subtaskId ? t("retrying") : t("retry")}
                    </button>
                  ) : null}
                  key={message.key}
                  messageId={message.subtaskId === null ? message.key : String(message.subtaskId)}
                  meta={message.role === "USER" ? t("you") : t("assistant")}
                  tone={message.role === "USER" ? "user" : "assistant"}
                >
                  {message.role === "USER" ? <p>{message.prompt}</p> : null}
                  {message.role === "ASSISTANT" && message.blocks.length === 0 &&
                  (message.status === "PENDING" || message.status === "RUNNING") ? (
                    <p className="typing-line"><Loader2 aria-hidden="true" size={16} />{t("streaming")}</p>
                  ) : null}
                  {message.role === "USER" ? <SubtaskContexts contexts={message.contexts} /> : null}
                </ChatMessage>
              );
            })}
          </div>
        )}
        {localError || socketError ? (
          <ChatMessage meta={t("error_title")} tone="system">
            <p className="form-error" role="alert">{localError ?? socketError}</p>
          </ChatMessage>
        ) : null}
      </div>

      <div className="chat-composer-wrap">
        {disconnected ? <p role="status">{t(chat.reconnecting ? "reconnecting" : "disconnected")}</p> : null}
        {agentUnavailable ? <p role="status">{t("agent_unavailable")}</p> : null}
        {taskRunning ? (
          <div className="generation-in-progress" role="status">
            <span>{t("task_running")}</span>
            {requestedTaskId !== null ? (
              <button disabled={chat.cancelling || disconnected} onClick={() => void handleCancel()} type="button">
                <Square aria-hidden="true" size={14} />
                {chat.cancelling ? t("cancelling") : t("cancel_generation")}
              </button>
            ) : null}
          </div>
        ) : null}
        <form aria-label={t("composer.label")} className="chat-composer" onSubmit={handleSubmit}>
          <div className="composer-box">
            <label className="sr-only" htmlFor="chat-composer">{t("composer.input_label")}</label>
            <AutoResizeTextarea
              aria-label={t("composer.input_label")}
              disabled={interactionDisabled}
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
            <div aria-label={t("composer.actions")} className="composer-toolbar composer-toolbar--wrap-safe" role="toolbar">
              <div className="composer-toolbar__left" data-composer-group="left">
                <AttachmentPicker
                  disabled={interactionDisabled || contextRecoveryError !== null}
                  loading={contextLoading}
                  onChange={setDraftContexts}
                  onPendingChange={setContextMutationPending}
                  onRetry={() => void recoverDrafts(viewRef.current)}
                  recoveryError={contextRecoveryError}
                  value={draftContexts}
                >
                  {requestedTaskId !== null ? (
                    <span aria-label={t("agentPicker.current", { name: lockedAgent?.name ?? t("agentPicker.none") })} className="agent-summary">
                      {lockedAgent?.name ?? t("agentPicker.none")}
                    </span>
                  ) : (
                    <AgentPicker
                      agents={agents}
                      disabled={interactionDisabled}
                      onClear={() => setSelectedAgentId(null)}
                      onSelect={(agent) => setSelectedAgentId(agent.id)}
                      selectedAgentId={selectedAgentId}
                    />
                  )}
                </AttachmentPicker>
              </div>
              <div className="composer-toolbar__right" data-composer-group="right">
                <ModelPicker
                  agentDefault={agentDefault}
                  disabled={interactionDisabled}
                  labels={{
                    agentDefault: t("modelPicker.agentDefault"),
                    followAgentDefault: t("modelPicker.followAgentDefault"),
                    listbox: t("modelPicker.listbox"),
                    modelUnavailable: t("modelPicker.modelUnavailable"),
                    noProviders: t("modelPicker.noProviders"),
                    selectModel: t("modelPicker.selectModel"),
                  }}
                  onOpenChange={setModelMenuOpen}
                  onSelect={(value) => void selectModel(value)}
                  open={modelMenuOpen}
                  providers={providers}
                  selected={selectedModelOverride}
                />
                <button aria-label={t("composer.send")} className="send-button" disabled={!canSend} type="submit">
                  {chat.sending ? <Loader2 aria-hidden="true" size={18} /> : <Send aria-hidden="true" size={18} />}
                </button>
              </div>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
