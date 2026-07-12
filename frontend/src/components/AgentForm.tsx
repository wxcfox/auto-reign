"use client";

import { useRef, useState, type FormEvent } from "react";

import { AgentHomeSelector } from "@/components/AgentHomeSelector";
import { KnowledgeScopeEditor } from "@/components/KnowledgeScopeEditor";
import { useTranslation } from "@/hooks/useTranslation";
import { MAX_PROMPT_LENGTH, MAX_RESOURCE_NAME_LENGTH } from "@/lib/limits";
import type {
  AgentResource,
  KnowledgeCollectionResource,
  ModelListResponse,
  WorkspaceResource,
} from "@/lib/types";
import {
  AgentFormValidationError,
  agentToFormState,
  buildAgentSubmission,
  type AgentFormErrorCode,
  type AgentFormState,
  type AgentSubmission,
} from "./agent-form-state";

export interface AgentFormProps {
  agent: AgentResource | null;
  models: ModelListResponse;
  workspaces: WorkspaceResource[];
  collections: KnowledgeCollectionResource[];
  saving: boolean;
  onCancel: () => void;
  onSubmit: (payload: AgentSubmission) => Promise<void>;
}

export function AgentForm(props: AgentFormProps) {
  const formIdentity =
    props.agent === null ? "new-agent" : `${props.agent.id}\u0000${props.agent.updated_at}`;
  return <AgentFormInstance key={formIdentity} {...props} />;
}

function AgentFormInstance({
  agent,
  models,
  workspaces,
  collections,
  saving,
  onCancel,
  onSubmit,
}: AgentFormProps) {
  const { t } = useTranslation("agents");
  const [state, setState] = useState<AgentFormState>(() => agentToFormState(agent));
  const [validationCode, setValidationCode] = useState<AgentFormErrorCode | null>(null);
  const [knowledgeAvailable, setKnowledgeAvailable] = useState(
    () =>
      agent === null ||
      agent.config.knowledge_scopes.every((scope) => scope.document_ids === null),
  );
  const submittingRef = useRef(false);
  const selectedProvider = models.providers.find(
    (item) => item.provider === state.defaultProvider,
  );
  const providerModels = selectedProvider?.models ?? [];
  const providerUnavailable =
    state.defaultModelMode === "custom" &&
    state.defaultProvider.length > 0 &&
    selectedProvider === undefined;
  const modelUnavailable =
    state.defaultModelMode === "custom" &&
    state.defaultModel.length > 0 &&
    !providerModels.includes(state.defaultModel);
  const workspaceUnavailable =
    state.homeMode === "existing" &&
    state.workspaceId.length > 0 &&
    !workspaces.some((workspace) => workspace.id === state.workspaceId);
  const availableCollectionIds = new Set(
    collections.map((collection) => collection.id),
  );
  const collectionUnavailable = state.knowledgeScopes.some(
    (scope) =>
      scope.collectionId.length > 0 &&
      !availableCollectionIds.has(scope.collectionId),
  );
  const hasUnavailableReference =
    providerUnavailable ||
    modelUnavailable ||
    workspaceUnavailable ||
    collectionUnavailable ||
    !knowledgeAvailable;

  function patch(next: Partial<AgentFormState>) {
    setState((current) => ({ ...current, ...next }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || hasUnavailableReference || submittingRef.current) {
      return;
    }
    submittingRef.current = true;
    try {
      const payload = buildAgentSubmission(state);
      setValidationCode(null);
      await onSubmit(payload);
    } catch (error) {
      if (error instanceof AgentFormValidationError) {
        setValidationCode(error.code);
        return;
      }
      throw error;
    } finally {
      submittingRef.current = false;
    }
  }

  return (
    <form className="agent-form" onSubmit={(event) => void submit(event)}>
      <label>
        {t("fields.name")}
        <input
          disabled={saving}
          maxLength={MAX_RESOURCE_NAME_LENGTH}
          onChange={(event) => patch({ name: event.target.value })}
          value={state.name}
        />
      </label>
      <label>
        {t("fields.system_prompt")}
        <textarea
          disabled={saving}
          maxLength={MAX_PROMPT_LENGTH}
          onChange={(event) => patch({ systemPrompt: event.target.value })}
          rows={14}
          value={state.systemPrompt}
        />
      </label>

      <fieldset className="agent-form-section" disabled={saving}>
        <legend>{t("fields.default_model")}</legend>
        <label>
          <input
            checked={state.defaultModelMode === "follow"}
            name="model-mode"
            onChange={() =>
              patch({
                defaultModelMode: "follow",
                defaultProvider: "",
                defaultModel: "",
              })
            }
            type="radio"
          />
          {t("model.follow_system")}
        </label>
        <label>
          <input
            checked={state.defaultModelMode === "custom"}
            name="model-mode"
            onChange={() => patch({ defaultModelMode: "custom" })}
            type="radio"
          />
          {t("model.specific")}
        </label>
        {state.defaultModelMode === "custom" ? (
          <div className="agent-model-grid">
            <label>
              {t("fields.provider")}
              <select
                onChange={(event) =>
                  patch({ defaultProvider: event.target.value, defaultModel: "" })
                }
                value={state.defaultProvider}
              >
                <option value="">{t("actions.select")}</option>
                {providerUnavailable ? (
                  <option value={state.defaultProvider}>
                    {t("model.unavailable_provider_option", {
                      provider: state.defaultProvider,
                    })}
                  </option>
                ) : null}
                {models.providers.map((provider) => (
                  <option key={provider.provider} value={provider.provider}>
                    {provider.provider}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("fields.model")}
              <select
                disabled={!state.defaultProvider}
                onChange={(event) => patch({ defaultModel: event.target.value })}
                value={state.defaultModel}
              >
                <option value="">{t("actions.select")}</option>
                {modelUnavailable ? (
                  <option value={state.defaultModel}>
                    {t("model.unavailable_model_option", { model: state.defaultModel })}
                  </option>
                ) : null}
                {providerModels.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}
        {providerUnavailable || modelUnavailable ? (
          <p
            aria-label={t("model.unavailable_label")}
            className="form-error"
            role="alert"
          >
            {t("model.unavailable", {
              provider: state.defaultProvider,
              model: state.defaultModel,
            })}
          </p>
        ) : null}
      </fieldset>

      <AgentHomeSelector
        disabled={saving}
        onChange={patch}
        value={state}
        workspaces={workspaces}
      />
      <KnowledgeScopeEditor
        collections={collections}
        disabled={saving}
        onAvailabilityChange={setKnowledgeAvailable}
        onChange={(knowledgeScopes) => {
          setKnowledgeAvailable(
            knowledgeScopes.every((scope) => scope.mode === "all"),
          );
          patch({ knowledgeScopes });
        }}
        value={state.knowledgeScopes}
      />

      {validationCode ? (
        <p className="form-error" role="alert">
          {t(`errors.${validationCode}`)}
        </p>
      ) : null}
      <div className="dialog-actions">
        <button className="button" disabled={saving} onClick={onCancel} type="button">
          {t("actions.cancel")}
        </button>
        <button
          className="button button-primary"
          disabled={saving || hasUnavailableReference}
          type="submit"
        >
          {saving ? t("actions.saving") : t("actions.save")}
        </button>
      </div>
    </form>
  );
}
