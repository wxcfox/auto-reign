"use client";

import { useTranslation } from "@/hooks/useTranslation";
import { MAX_PROMPT_LENGTH, MAX_RESOURCE_NAME_LENGTH } from "@/lib/limits";
import type { WorkspaceResource } from "@/lib/types";
import type { AgentFormState, HomeMode } from "./agent-form-state";

export interface AgentHomeSelectorProps {
  value: AgentFormState;
  workspaces: WorkspaceResource[];
  disabled?: boolean;
  onChange: (patch: Partial<AgentFormState>) => void;
}

export function AgentHomeSelector({
  value,
  workspaces,
  disabled = false,
  onChange,
}: AgentHomeSelectorProps) {
  const { t } = useTranslation("agents");
  const setMode = (homeMode: HomeMode) => onChange({ homeMode });
  const workspaceUnavailable =
    value.homeMode === "existing" &&
    value.workspaceId.length > 0 &&
    !workspaces.some((workspace) => workspace.id === value.workspaceId);

  return (
    <fieldset className="agent-form-section" disabled={disabled}>
      <legend>{t("home.title")}</legend>
      <label>
        <input
          checked={value.homeMode === "none"}
          name="home-mode"
          onChange={() => setMode("none")}
          type="radio"
        />
        {t("home.none")}
      </label>
      <label>
        <input
          checked={value.homeMode === "existing"}
          name="home-mode"
          onChange={() => setMode("existing")}
          type="radio"
        />
        {t("home.existing")}
      </label>
      {value.homeMode === "existing" ? (
        <div className="agent-home-existing">
          <select
            aria-label={t("home.workspace")}
            disabled={workspaces.length === 0 && !workspaceUnavailable}
            onChange={(event) => onChange({ workspaceId: event.target.value })}
            value={value.workspaceId}
          >
            <option value="">{t("home.select_workspace")}</option>
            {workspaceUnavailable ? (
              <option value={value.workspaceId}>
                {t("home.unavailable_workspace_option", { id: value.workspaceId })}
              </option>
            ) : null}
            {workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name}
              </option>
            ))}
          </select>
          {workspaceUnavailable ? (
            <p
              aria-label={t("home.workspace_unavailable_label")}
              className="form-error"
              role="alert"
            >
              {t("home.workspace_unavailable", { id: value.workspaceId })}
            </p>
          ) : workspaces.length === 0 ? (
            <p className="agent-form-hint">{t("home.no_workspaces")}</p>
          ) : null}
        </div>
      ) : null}
      <label>
        <input
          checked={value.homeMode === "create"}
          name="home-mode"
          onChange={() => setMode("create")}
          type="radio"
        />
        {t("home.create")}
      </label>
      {value.homeMode === "create" ? (
        <div className="agent-home-create">
          <label>
            {t("home.workspace_name")}
            <input
              maxLength={MAX_RESOURCE_NAME_LENGTH}
              onChange={(event) => onChange({ newWorkspaceName: event.target.value })}
              value={value.newWorkspaceName}
            />
          </label>
          <label>
            {t("home.initial_agents_md")}
            <textarea
              maxLength={MAX_PROMPT_LENGTH}
              onChange={(event) => onChange({ initialAgentsMd: event.target.value })}
              rows={10}
              value={value.initialAgentsMd}
            />
          </label>
        </div>
      ) : null}
    </fieldset>
  );
}
