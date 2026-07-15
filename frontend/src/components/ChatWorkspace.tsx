"use client";

import { Loader2, Send } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { AgentPicker } from "@/components/AgentPicker";
import { AttachmentPicker } from "@/components/AttachmentPicker";
import { AutoResizeTextarea } from "@/components/AutoResizeTextarea";
import { ChatMessage } from "@/components/ChatMessage";
import { MarkdownView } from "@/components/MarkdownView";
import { ModelPicker } from "@/components/ModelPicker";
import { useTranslation } from "@/hooks/useTranslation";
import {
  getConversation,
  getModels,
  listAttachmentDrafts,
  listAgents,
  sendConversationStream,
  setConversationModel,
} from "@/lib/api";
import { notifyConversationsChanged } from "@/lib/conversation-events";
import { getErrorMessage } from "@/lib/error-messages";
import { ApiError } from "@/lib/api-error";
import type {
  AcceptedGeneration,
  Agent,
  Attachment,
  ConversationHistoryItem,
  ConversationMessage,
  ModelProvider,
  ModelRef,
} from "@/lib/types";

type ChatWorkspaceProps = {
  sessionId?: string;
};

type AgentSummary = ConversationHistoryItem["agent"];

function modelLabel(model: ModelRef | null) {
  return model ? `${model.provider} / ${model.model}` : null;
}

function sameModel(left: ModelRef | null, right: ModelRef | null) {
  return left?.provider === right?.provider && left?.model === right?.model;
}

export function ChatWorkspace({ sessionId }: ChatWorkspaceProps = {}) {
  const router = useRouter();
  const { t } = useTranslation("chat");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [platformDefault, setPlatformDefault] = useState<ModelRef | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [lockedAgent, setLockedAgent] = useState<AgentSummary | null>(null);
  const [selectedModelOverride, setSelectedModelOverride] = useState<ModelRef | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(sessionId ?? null);
  const [conversationStatus, setConversationStatus] = useState<"idle" | "generating">("idle");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [composerValue, setComposerValue] = useState("");
  const [draftAttachments, setDraftAttachments] = useState<Attachment[]>([]);
  const [attachmentLoading, setAttachmentLoading] = useState(true);
  const [attachmentRecoveryError, setAttachmentRecoveryError] = useState<string | null>(null);
  const [attachmentMutationPending, setAttachmentMutationPending] = useState(false);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [sending, setSending] = useState(false);
  const [modelUpdating, setModelUpdating] = useState(false);
  const [awaitingSessionId, setAwaitingSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const viewGenerationRef = useRef(0);
  const sendOperationRef = useRef<symbol | null>(null);
  const modelOperationRef = useRef<symbol | null>(null);
  const localMessageSequenceRef = useRef(0);
  const translationRef = useRef(t);
  translationRef.current = t;

  useEffect(() => {
    const viewGeneration = viewGenerationRef.current + 1;
    viewGenerationRef.current = viewGeneration;
    sendOperationRef.current = null;
    modelOperationRef.current = null;
    setLoading(true);
    setLoadFailed(false);
    setSending(false);
    setModelUpdating(false);
    setAwaitingSessionId(null);
    setAgents([]);
    setProviders([]);
    setPlatformDefault(null);
    setSelectedAgentId(null);
    setLockedAgent(null);
    setSelectedModelOverride(null);
    setConversationId(sessionId ?? null);
    setConversationStatus("idle");
    setMessages([]);
    setComposerValue("");
    setDraftAttachments([]);
    setAttachmentLoading(true);
    setAttachmentRecoveryError(null);
    setAttachmentMutationPending(false);
    setStreamingText(null);
    setModelMenuOpen(false);
    setError(null);

    const load = async () => {
      const agentRequest = listAgents("visible");
      const modelRequest = getModels();
      if (sessionId) {
        const auxiliaryRequest = Promise.allSettled([agentRequest, modelRequest]);
        try {
          const detail = await getConversation(sessionId);
          if (viewGenerationRef.current !== viewGeneration) {
            return;
          }
          setConversationId(detail.id);
          setConversationStatus(detail.status);
          setLockedAgent(detail.agent);
          setSelectedAgentId(detail.agent.id);
          setSelectedModelOverride(detail.model_override);
          setMessages(detail.messages);
        } catch (loadError) {
          if (viewGenerationRef.current === viewGeneration) {
            setLoadFailed(true);
            setError(
              getErrorMessage(loadError, translationRef.current, "chat:errors.load"),
            );
          }
        } finally {
          if (viewGenerationRef.current === viewGeneration) {
            setLoading(false);
          }
        }

        const [agentResult, modelResult] = await auxiliaryRequest;
        if (viewGenerationRef.current !== viewGeneration) {
          return;
        }
        if (agentResult.status === "fulfilled") {
          setAgents(agentResult.value.agents);
        }
        if (modelResult.status === "fulfilled") {
          setProviders(modelResult.value.providers);
          setPlatformDefault(modelResult.value.default);
        }
        if (agentResult.status === "rejected" || modelResult.status === "rejected") {
          setError((current) => current ?? translationRef.current("errors.options_load"));
        }
        return;
      }

      try {
        const [agentResponse, modelResponse] = await Promise.all([agentRequest, modelRequest]);
        if (viewGenerationRef.current !== viewGeneration) {
          return;
        }
        setAgents(agentResponse.agents);
        setProviders(modelResponse.providers);
        setPlatformDefault(modelResponse.default);
        setSelectedAgentId(null);
      } catch (loadError) {
        if (viewGenerationRef.current === viewGeneration) {
          setLoadFailed(true);
          setError(
            getErrorMessage(loadError, translationRef.current, "chat:errors.load"),
          );
        }
      } finally {
        if (viewGenerationRef.current === viewGeneration) {
          setLoading(false);
        }
      }
    };

    const loadAttachments = async () => {
      try {
        const drafts = await listAttachmentDrafts();
        if (viewGenerationRef.current === viewGeneration) {
          setDraftAttachments(drafts);
          setAttachmentRecoveryError(null);
        }
      } catch {
        if (viewGenerationRef.current === viewGeneration) {
          setAttachmentRecoveryError(translationRef.current("attachments.loadFailed"));
        }
      } finally {
        if (viewGenerationRef.current === viewGeneration) {
          setAttachmentLoading(false);
        }
      }
    };

    void load();
    void loadAttachments();
    return () => {
      if (viewGenerationRef.current === viewGeneration) {
        viewGenerationRef.current += 1;
      }
    };
  }, [sessionId]);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (!transcript) {
      return;
    }
    transcript.scrollTo?.({ top: transcript.scrollHeight, behavior: "smooth" });
    transcript.scrollTop = transcript.scrollHeight;
  }, [messages.length, streamingText, sending]);

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? null,
    [agents, selectedAgentId],
  );
  const selectedAgentDefinition = useMemo(
    () => agents.find((agent) => agent.id === (lockedAgent?.id ?? selectedAgentId)) ?? null,
    [agents, lockedAgent?.id, selectedAgentId],
  );
  const resolvedAgentDefault = selectedAgentDefinition
    ? selectedAgentDefinition.config.default_model ?? platformDefault
    : lockedAgent === null
      ? platformDefault
      : lockedAgent.is_available
        ? platformDefault
        : null;
  const agentUnavailable = lockedAgent !== null && !lockedAgent.is_available;
  const generationInProgress = conversationStatus === "generating";
  const mutationPending = sending || modelUpdating;
  const interactionDisabled =
    loading ||
    loadFailed ||
    agentUnavailable ||
    generationInProgress ||
    mutationPending ||
    attachmentLoading ||
    attachmentRecoveryError !== null ||
    attachmentMutationPending ||
    awaitingSessionId !== null;
  const resolvedModel = selectedModelOverride ?? resolvedAgentDefault;
  const canSend =
    Boolean(composerValue.trim()) &&
    draftAttachments.length <= 10 &&
    resolvedModel !== null &&
    !interactionDisabled;
  const currentModelLabel =
    modelLabel(resolvedModel) ?? t("modelPicker.modelUnavailable");

  async function retryAttachmentRecovery() {
    if (attachmentLoading || attachmentMutationPending) {
      return;
    }
    const viewGeneration = viewGenerationRef.current;
    setAttachmentLoading(true);
    setAttachmentRecoveryError(null);
    try {
      const drafts = await listAttachmentDrafts();
      if (viewGenerationRef.current === viewGeneration) {
        setDraftAttachments(drafts);
      }
    } catch {
      if (viewGenerationRef.current === viewGeneration) {
        setAttachmentRecoveryError(translationRef.current("attachments.loadFailed"));
      }
    } finally {
      if (viewGenerationRef.current === viewGeneration) {
        setAttachmentLoading(false);
      }
    }
  }

  async function selectModel(value: ModelRef | null) {
    if (interactionDisabled || modelOperationRef.current || sendOperationRef.current) {
      return;
    }
    if (!conversationId) {
      setSelectedModelOverride(value);
      return;
    }
    if (sameModel(value, selectedModelOverride)) {
      return;
    }

    const operation = Symbol("model-update");
    const viewGeneration = viewGenerationRef.current;
    modelOperationRef.current = operation;
    setModelUpdating(true);
    setError(null);
    try {
      const updated = await setConversationModel(conversationId, value);
      if (viewGenerationRef.current !== viewGeneration || modelOperationRef.current !== operation) {
        return;
      }
      setSelectedModelOverride(updated.model_override);
      setConversationStatus(updated.status);
      setLockedAgent(updated.agent);
      setMessages(updated.messages);
    } catch (updateError) {
      if (viewGenerationRef.current === viewGeneration && modelOperationRef.current === operation) {
        if (updateError instanceof ApiError && updateError.code === "agent_unavailable") {
          setLockedAgent((current) => current ? { ...current, is_available: false } : current);
        }
        if (updateError instanceof ApiError && updateError.code === "generation_in_progress") {
          setConversationStatus("generating");
        }
        setError(
          getErrorMessage(updateError, translationRef.current, "chat:errors.model_update"),
        );
      }
    } finally {
      if (modelOperationRef.current === operation) {
        modelOperationRef.current = null;
        if (viewGenerationRef.current === viewGeneration) {
          setModelUpdating(false);
        }
      }
    }
  }

  async function handleSubmit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const text = composerValue.trim();
    const turnAgent = lockedAgent ?? (selectedAgent
      ? { id: selectedAgent.id, name: selectedAgent.name, is_available: true }
      : null);
    if (
      !text ||
      resolvedModel === null ||
      interactionDisabled ||
      sendOperationRef.current ||
      modelOperationRef.current
    ) {
      return;
    }

    const operation = Symbol("send");
    const viewGeneration = viewGenerationRef.current;
    const requestConversationId = conversationId;
    const requestModel = selectedModelOverride ?? resolvedAgentDefault;
    const submittedAttachments = [...draftAttachments];
    const submittedAttachmentIds = submittedAttachments.map((attachment) => attachment.id);
    const timestamp = new Date().toISOString();
    localMessageSequenceRef.current += 1;
    const localSequence = localMessageSequenceRef.current;
    const optimisticUser: ConversationMessage = {
      id: `local-user-${localSequence}`,
      role: "user",
      status: "completed",
      content: text,
      attachments: [],
      provider: null,
      model: null,
      created_at: timestamp,
      updated_at: timestamp,
      metadata: { local: true },
    };
    let streamed = "";
    const acceptedState: { receipt: AcceptedGeneration | null } = { receipt: null };

    sendOperationRef.current = operation;
    setMessages((current) => [...current, optimisticUser]);
    setComposerValue("");
    setStreamingText("");
    setSending(true);
    setError(null);
    try {
      const result = await sendConversationStream(
        {
          text,
          conversation_id: requestConversationId ?? undefined,
          agent_id: requestConversationId ? undefined : (turnAgent?.id ?? undefined),
          model_override: requestConversationId ? undefined : selectedModelOverride,
          attachment_ids: submittedAttachmentIds,
        },
        {
          onAccepted: (receipt) => {
            if (
              viewGenerationRef.current !== viewGeneration ||
              sendOperationRef.current !== operation
            ) {
              return;
            }
            acceptedState.receipt = receipt;
            const acceptedIds = new Set(receipt.attachment_ids);
            const submittedIds = new Set(submittedAttachmentIds);
            if (receipt.attachment_ids.some((attachmentId) => !submittedIds.has(attachmentId))) {
              console.warn("attachment_receipt_included_unsubmitted_id");
            }
            setDraftAttachments((current) =>
              current.filter((attachment) => !acceptedIds.has(attachment.id)),
            );
            if (receipt.user_message_id !== null) {
              const acceptedAttachments = submittedAttachments
                .filter((attachment) => acceptedIds.has(attachment.id))
                .map((attachment) => ({
                  ...attachment,
                  message_id: receipt.user_message_id,
                }));
              setMessages((current) =>
                current.map((message) =>
                  message.id === optimisticUser.id
                    ? {
                        ...message,
                        id: receipt.user_message_id as string,
                        attachments: acceptedAttachments,
                        metadata: { ...message.metadata, local: false },
                      }
                    : message,
                ),
              );
            }
            setConversationId(receipt.conversation_id);
            setLockedAgent(turnAgent);
          },
          onDelta: (delta) => {
            streamed += delta;
            if (
              viewGenerationRef.current === viewGeneration &&
              sendOperationRef.current === operation
            ) {
              setStreamingText(streamed);
            }
          },
        },
      );
      notifyConversationsChanged();
      if (viewGenerationRef.current !== viewGeneration || sendOperationRef.current !== operation) {
        return;
      }
      setConversationId(result.conversation_id);
      setConversationStatus("idle");
      setLockedAgent(turnAgent);
      setMessages((current) => [...current, result.message]);
      if (!requestConversationId) {
        setAwaitingSessionId(result.conversation_id);
        router.replace(`/chat?session=${result.conversation_id}`, { scroll: false });
      }
    } catch (sendError) {
      notifyConversationsChanged();
      if (viewGenerationRef.current !== viewGeneration || sendOperationRef.current !== operation) {
        return;
      }
      if (sendError instanceof ApiError && sendError.code === "agent_unavailable") {
        if (requestConversationId) {
          setLockedAgent((current) => current ? { ...current, is_available: false } : current);
        } else {
          const availableAgents = turnAgent
            ? agents.filter((agent) => agent.id !== turnAgent.id)
            : agents;
          setAgents(availableAgents);
          setSelectedAgentId(availableAgents[0]?.id ?? null);
        }
      }
      if (sendError instanceof ApiError && sendError.code === "generation_in_progress") {
        setConversationStatus("generating");
      }
      const acceptedConversationId =
        acceptedState.receipt?.conversation_id ??
        (sendError instanceof ApiError ? sendError.conversationId : undefined);
      if (!requestConversationId && acceptedConversationId) {
        setConversationId(acceptedConversationId);
        setConversationStatus("idle");
        setLockedAgent(turnAgent);
        setAwaitingSessionId(acceptedConversationId);
        router.replace(`/chat?session=${acceptedConversationId}`, { scroll: false });
      }
      const failedAt = new Date().toISOString();
      const failedAssistant: ConversationMessage = {
        id:
          acceptedState.receipt?.assistant_message_id ??
          (sendError instanceof ApiError && sendError.assistantMessageId
            ? sendError.assistantMessageId
            : `local-assistant-failed-${localSequence}`),
        role: "assistant",
        status: "failed",
        content: streamed,
        attachments: [],
        provider: requestModel?.provider ?? null,
        model: requestModel?.model ?? null,
        created_at: failedAt,
        updated_at: failedAt,
        metadata: { local: true },
      };
      setMessages((current) => [...current, failedAssistant]);
      setError(getErrorMessage(sendError, translationRef.current, "chat:errors.send"));
    } finally {
      if (sendOperationRef.current === operation) {
        sendOperationRef.current = null;
        if (viewGenerationRef.current === viewGeneration) {
          setStreamingText(null);
          setSending(false);
        }
      }
    }
  }

  return (
    <div className="chat-workspace general-chat-workspace">
      <header className="chat-topbar">
        <span className="chat-topbar-title">{t("title")}</span>
        <span className="chat-topbar-model">{currentModelLabel}</span>
      </header>

      <div className="chat-transcript" ref={transcriptRef}>
        {messages.length === 0 && streamingText === null ? (
          <section className="chat-empty" aria-label={t("empty_label")}>
            <h2>{loading ? t("loading") : t("empty_title")}</h2>
          </section>
        ) : (
          <div className="chat-thread">
            {messages.map((message) => (
              <ChatMessage
                attachments={message.attachments}
                failed={message.role === "assistant" && message.status === "failed"}
                failedLabel={t("failed_response")}
                key={message.id}
                messageId={message.id}
                meta={message.role === "user" ? t("you") : t("assistant")}
                tone={message.role}
              >
                {message.role === "assistant" ? (
                  message.content ? <MarkdownView content={message.content} /> : null
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
        {agentUnavailable ? (
          <p className="agent-unavailable" role="status">{t("agent_unavailable")}</p>
        ) : null}
        {generationInProgress ? (
          <p className="generation-in-progress" role="status">
            {t("generation_in_progress")}
          </p>
        ) : null}
        {!loading && !conversationId && agents.length === 0 ? (
          <p className="agent-unavailable" role="status">{t("no_agents")}</p>
        ) : null}
        <form className="chat-composer" aria-label={t("composer.label")} onSubmit={handleSubmit}>
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
            <div
              aria-label={t("composer.actions")}
              className="composer-toolbar composer-toolbar--wrap-safe"
              role="toolbar"
            >
              <div className="composer-toolbar__left" data-composer-group="left">
                <AttachmentPicker
                  disabled={interactionDisabled || attachmentRecoveryError !== null}
                  key={sessionId ?? "new-conversation"}
                  loading={attachmentLoading}
                  onChange={setDraftAttachments}
                  onPendingChange={setAttachmentMutationPending}
                  onRetry={() => void retryAttachmentRecovery()}
                  recoveryError={attachmentRecoveryError}
                  value={draftAttachments}
                >
                  {conversationId ? (
                    <span className="agent-summary" aria-label={t("agentPicker.current", {
                      name: lockedAgent?.name ?? t("agentPicker.none", { defaultValue: "No agent" }),
                    })}>
                      {lockedAgent?.name ?? t("agentPicker.none", { defaultValue: "No agent" })}
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
                  agentDefault={resolvedAgentDefault}
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
                <button
                  aria-label={t("composer.send")}
                  className="send-button"
                  disabled={!canSend}
                  type="submit"
                >
                  {sending ? (
                    <Loader2 aria-hidden="true" size={18} />
                  ) : (
                    <Send aria-hidden="true" size={18} />
                  )}
                </button>
              </div>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
