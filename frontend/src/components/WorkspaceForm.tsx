"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { createWorkspace, updateWorkspace } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import { MAX_PROMPT_LENGTH, MAX_RESOURCE_NAME_LENGTH } from "@/lib/limits";
import type { Workspace, WorkspaceScope } from "@/lib/types";

export type WorkspaceFormProps = {
  onCancel?: () => void;
  onSaved?: (workspace: Workspace) => void;
  onSavingChange?: (saving: boolean) => void;
  scope: WorkspaceScope;
  workspace?: Workspace | null;
};

export function WorkspaceForm({
  onCancel,
  onSaved,
  onSavingChange,
  scope,
  workspace = null,
}: WorkspaceFormProps) {
  const { t } = useTranslation("workspaces");
  const hintId = useId();
  const [name, setName] = useState(workspace?.name ?? "");
  const [initialAgentsMd, setInitialAgentsMd] = useState(
    workspace?.config.initial_agents_md ?? "",
  );
  const [saving, setSaving] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const operationRef = useRef(0);
  const savingRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      operationRef.current += 1;
      savingRef.current = false;
    };
  }, []);

  useEffect(() => {
    operationRef.current += 1;
    savingRef.current = false;
    setName(workspace?.name ?? "");
    setInitialAgentsMd(workspace?.config.initial_agents_md ?? "");
    setSaving(false);
    setErrorKey(null);
    onSavingChange?.(false);
  }, [onSavingChange, scope, workspace]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedAgentsMd = initialAgentsMd.trim();
    if (savingRef.current || !trimmedName || !trimmedAgentsMd) {
      return;
    }

    const operation = ++operationRef.current;
    savingRef.current = true;
    setSaving(true);
    setErrorKey(null);
    onSavingChange?.(true);
    try {
      const payload = {
        name: trimmedName,
        config: {
          workspace_type: "agent_home" as const,
          initial_agents_md: trimmedAgentsMd,
        },
      };
      const saved =
        workspace === null
          ? await createWorkspace(scope, payload)
          : await updateWorkspace(scope, workspace.id, {
              ...payload,
              is_active: workspace.is_active,
            });
      if (!mountedRef.current || operationRef.current !== operation) {
        return;
      }
      if (workspace === null) {
        setName("");
        setInitialAgentsMd("");
      }
      onSaved?.(saved);
    } catch (error) {
      if (!mountedRef.current || operationRef.current !== operation) {
        return;
      }
      setErrorKey(
        error instanceof ApiError && error.code === "resource_name_taken"
          ? "form.nameTaken"
          : "form.saveError",
      );
    } finally {
      if (mountedRef.current && operationRef.current === operation) {
        savingRef.current = false;
        setSaving(false);
        onSavingChange?.(false);
      }
    }
  }

  const editing = workspace !== null;
  const submitDisabled = saving || !name.trim() || !initialAgentsMd.trim();
  const hintKey =
    scope === "global"
      ? "form.initialAgentsMdHintGlobal"
      : "form.initialAgentsMdHint";

  return (
    <form className="workspace-form" onSubmit={(event) => void handleSubmit(event)}>
      <label>
        {t("form.name")}
        <input
          autoFocus
          maxLength={MAX_RESOURCE_NAME_LENGTH}
          onChange={(event) => setName(event.target.value)}
          required
          value={name}
        />
      </label>
      <label>
        {t("form.initialAgentsMd")}
        <textarea
          aria-describedby={hintId}
          maxLength={MAX_PROMPT_LENGTH}
          onChange={(event) => setInitialAgentsMd(event.target.value)}
          required
          rows={10}
          value={initialAgentsMd}
        />
        <small className="form-hint" id={hintId}>
          {t(hintKey)}
        </small>
      </label>
      {errorKey ? (
        <p className="form-error" role="alert">
          {t(errorKey)}
        </p>
      ) : null}
      <div className="management-form-actions">
        {onCancel ? (
          <button
            className="button"
            disabled={saving}
            onClick={onCancel}
            type="button"
          >
            {t("actions.cancel")}
          </button>
        ) : null}
        <button
          className="workspace-primary-action"
          disabled={submitDisabled}
          type="submit"
        >
          {saving
            ? t("actions.saving")
            : editing
              ? t("actions.save")
              : t("actions.create")}
        </button>
      </div>
    </form>
  );
}
