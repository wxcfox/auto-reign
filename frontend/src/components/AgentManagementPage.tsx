"use client";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { useRouter } from "next/navigation";

import { AgentForm } from "@/components/AgentForm";
import type { AgentSubmission } from "@/components/agent-form-state";
import { useTranslation } from "@/hooks/useTranslation";
import {
  createAgent,
  createGlobalAgent,
  createWorkspace,
  deleteAgent,
  getModels,
  listAgents,
  listKnowledgeCollections,
  listWorkspaces,
  updateAgent,
} from "@/lib/api";
import type {
  AgentResource,
  KnowledgeCollectionResource,
  ModelListResponse,
  WorkspaceResource,
} from "@/lib/types";

export type ManagementScope = "private" | "global";

type AgentManagementPageProps = {
  initialCreate?: boolean;
  scope: ManagementScope;
};

const scopes = {
  private: { agents: "owned", resources: "visible" },
  global: { agents: "global", resources: "global" },
} as const;

const FOCUSABLE_SELECTOR =
  "button, input:not([type='hidden']), select, textarea, [tabindex]:not([tabindex='-1'])";

function focusableElements(container: HTMLElement | null) {
  return Array.from(
    container?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [],
  ).filter((element) => !element.matches(":disabled"));
}

export function AgentManagementPage({
  initialCreate = false,
  scope,
}: AgentManagementPageProps) {
  const router = useRouter();
  const { t } = useTranslation("agents");
  const dialogTitleId = useId();
  const [agents, setAgents] = useState<AgentResource[]>([]);
  const [workspaces, setWorkspaces] = useState<WorkspaceResource[]>([]);
  const [collections, setCollections] = useState<KnowledgeCollectionResource[]>([]);
  const [models, setModels] = useState<ModelListResponse | null>(null);
  const [editing, setEditing] = useState<AgentResource | "new" | null>(() =>
    scope === "private" && initialCreate ? "new" : null,
  );
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [saving, setSaving] = useState(false);
  const [pendingAgentId, setPendingAgentId] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const lifecycleGenerationRef = useRef(0);
  const loadGenerationRef = useRef(0);
  const mutationSequenceRef = useRef(0);
  const activeMutationRef = useRef<number | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const previousScopeRef = useRef(scope);
  const initialCreateRouteRef = useRef(scope === "private" && initialCreate);

  const load = useCallback(
    async (showLoading = true) => {
      const generation = ++loadGenerationRef.current;
      if (mountedRef.current && showLoading) {
        setLoadState("loading");
      }
      try {
        const [agentResult, workspaceResult, collectionResult, modelResult] =
          await Promise.all([
            listAgents(scopes[scope].agents, { includeInactive: true }),
            listWorkspaces(scopes[scope].resources),
            listKnowledgeCollections(scopes[scope].resources),
            getModels(),
          ]);
        if (!mountedRef.current || loadGenerationRef.current !== generation) {
          return false;
        }
        setAgents(agentResult.agents);
        setWorkspaces(workspaceResult.workspaces.filter((workspace) => workspace.is_active));
        setCollections(
          collectionResult.collections.filter((collection) => collection.is_active),
        );
        setModels(modelResult);
        setLoadState("ready");
        return true;
      } catch {
        if (mountedRef.current && loadGenerationRef.current === generation) {
          if (showLoading) {
            setLoadState("error");
          } else {
            setPageError((current) => current ?? t("states.load_failed"));
          }
        }
        return false;
      }
    },
    [scope, t],
  );

  useEffect(() => {
    const lifecycleGeneration = ++lifecycleGenerationRef.current;
    const scopeChanged = previousScopeRef.current !== scope;
    previousScopeRef.current = scope;
    mountedRef.current = true;
    activeMutationRef.current = null;
    if (scopeChanged) {
      initialCreateRouteRef.current = false;
      setEditing(null);
    }
    setDialogError(null);
    setPageError(null);
    setSaving(false);
    setPendingAgentId(null);
    void load();
    return () => {
      if (lifecycleGenerationRef.current === lifecycleGeneration) {
        mountedRef.current = false;
        lifecycleGenerationRef.current += 1;
        activeMutationRef.current = null;
      }
      loadGenerationRef.current += 1;
    };
  }, [load, scope]);

  useEffect(() => {
    if (editing !== null) {
      const [first] = focusableElements(dialogRef.current);
      first?.focus();
      return;
    }
    const trigger = returnFocusRef.current;
    if (trigger?.isConnected) {
      trigger.focus();
    }
  }, [editing, loadState]);

  function startMutation() {
    if (activeMutationRef.current !== null) {
      return null;
    }
    const mutation = ++mutationSequenceRef.current;
    activeMutationRef.current = mutation;
    return mutation;
  }

  function isCurrentMutation(mutation: number, lifecycleGeneration: number) {
    return (
      mountedRef.current &&
      lifecycleGenerationRef.current === lifecycleGeneration &&
      activeMutationRef.current === mutation
    );
  }

  function finishMutation(mutation: number) {
    if (activeMutationRef.current === mutation) {
      activeMutationRef.current = null;
    }
  }

  function openEditor(
    target: AgentResource | "new",
    trigger: HTMLElement,
  ) {
    if (activeMutationRef.current !== null) {
      return;
    }
    returnFocusRef.current = trigger;
    setDialogError(null);
    setEditing(target);
  }

  function clearInitialCreateRoute() {
    if (scope === "private" && initialCreateRouteRef.current) {
      initialCreateRouteRef.current = false;
      router.replace("/agents");
    }
  }

  function closeEditor() {
    if (saving || activeMutationRef.current !== null) {
      return;
    }
    setDialogError(null);
    setEditing(null);
    clearInitialCreateRoute();
  }

  async function save(submission: AgentSubmission) {
    if (editing === null) {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    const target = editing;
    if (mountedRef.current) {
      setSaving(true);
      setDialogError(null);
    }
    let agentPayload = submission.agent;
    let createdHome: WorkspaceResource | null = null;
    try {
      if (submission.workspace !== null) {
        createdHome = await createWorkspace(scope, submission.workspace);
        if (!isCurrentMutation(mutation, lifecycleGeneration)) {
          return;
        }
        agentPayload = {
          ...agentPayload,
          config: {
            ...agentPayload.config,
            home_workspace_id: createdHome.id,
          },
        };
      }
      if (target === "new") {
        if (scope === "global") {
          await createGlobalAgent(agentPayload);
        } else {
          await createAgent(agentPayload);
        }
      } else {
        await updateAgent(target.id, {
          ...agentPayload,
          is_active: target.is_active,
        });
      }
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setEditing(null);
        setDialogError(null);
        if (target === "new") {
          clearInitialCreateRoute();
        }
        await load(false);
      }
    } catch {
      if (!isCurrentMutation(mutation, lifecycleGeneration)) {
        return;
      }
      if (createdHome !== null) {
        setEditing(null);
        setDialogError(null);
        if (target === "new") {
          clearInitialCreateRoute();
        }
        setPageError(
          t("errors.workspace_created_agent_failed", { name: createdHome.name }),
        );
        await load(false);
      } else {
        setDialogError(t("errors.save_failed"));
      }
    } finally {
      const shouldUpdate = isCurrentMutation(mutation, lifecycleGeneration);
      finishMutation(mutation);
      if (shouldUpdate) {
        setSaving(false);
      }
    }
  }

  async function setActive(agent: AgentResource) {
    if (!agent.can_manage) {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    if (mountedRef.current) {
      setPendingAgentId(agent.id);
      setPageError(null);
    }
    try {
      await updateAgent(agent.id, {
        name: agent.name,
        config: agent.config,
        is_active: !agent.is_active,
      });
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        await load(false);
      }
    } catch {
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setPageError(t("errors.status_failed"));
      }
    } finally {
      const shouldUpdate = isCurrentMutation(mutation, lifecycleGeneration);
      finishMutation(mutation);
      if (shouldUpdate) {
        setPendingAgentId(null);
      }
    }
  }

  async function remove(agent: AgentResource) {
    if (
      !agent.can_manage ||
      activeMutationRef.current !== null ||
      !window.confirm(t("actions.delete_confirm", { name: agent.name }))
    ) {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    if (mountedRef.current) {
      setPendingAgentId(agent.id);
      setPageError(null);
    }
    try {
      await deleteAgent(agent.id);
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        await load(false);
      }
    } catch {
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setPageError(t("errors.delete_failed"));
      }
    } finally {
      const shouldUpdate = isCurrentMutation(mutation, lifecycleGeneration);
      finishMutation(mutation);
      if (shouldUpdate) {
        setPendingAgentId(null);
      }
    }
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeEditor();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    const focusable = focusableElements(dialogRef.current);
    if (focusable.length === 0) {
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function handleBackdrop(event: MouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget) {
      closeEditor();
    }
  }

  if (loadState === "loading") {
    return (
      <section className="management-page">
        <p role="status">{t("states.loading")}</p>
      </section>
    );
  }
  if (loadState === "error") {
    return (
      <section className="management-page management-load-error" role="alert">
        <p>{t("states.load_failed")}</p>
        <button className="button" onClick={() => void load()} type="button">
          {t("actions.retry")}
        </button>
      </section>
    );
  }
  if (models === null) {
    return null;
  }

  const dialogTitle =
    editing === "new"
      ? t("dialog.create_title")
      : editing === null
        ? ""
        : t("dialog.edit_title", { name: editing.name });
  const controlsDisabled = pendingAgentId !== null || saving;

  return (
    <section className="management-page" aria-labelledby="agent-management-title">
      <div
        aria-hidden={editing !== null}
        className="management-content"
        inert={editing !== null ? true : undefined}
      >
        <header className="management-header">
          <div>
            <h1 id="agent-management-title">
              {scope === "global" ? t("global.title") : t("personal.title")}
            </h1>
            <p>
              {scope === "global" ? t("global.summary") : t("personal.summary")}
            </p>
          </div>
          <button
            className="button button-primary"
            disabled={controlsDisabled}
            onClick={(event) => openEditor("new", event.currentTarget)}
            type="button"
          >
            {scope === "global" ? t("actions.create_global") : t("actions.create")}
          </button>
        </header>

        {pageError ? (
          <p className="form-error" role="alert">
            {pageError}
          </p>
        ) : null}

        {agents.length === 0 ? (
          <p className="empty-state">{t("states.empty")}</p>
        ) : (
          <ul className="management-list">
            {agents.map((agent) => {
              const rowPending = pendingAgentId === agent.id;
              return (
                <li key={agent.id}>
                  <div className="management-list__summary">
                    <strong>{agent.name}</strong>
                    <span>{agent.is_active ? t("states.active") : t("states.inactive")}</span>
                  </div>
                  <div className="management-list__actions">
                    <button
                      aria-label={t("actions.edit_label", { name: agent.name })}
                      className="button"
                      disabled={!agent.can_manage || controlsDisabled}
                      onClick={(event) => openEditor(agent, event.currentTarget)}
                      type="button"
                    >
                      {t("actions.edit")}
                    </button>
                    <button
                      aria-label={
                        agent.is_active
                          ? t("actions.disable_label", { name: agent.name })
                          : t("actions.enable_label", { name: agent.name })
                      }
                      className="button"
                      disabled={!agent.can_manage || controlsDisabled}
                      onClick={() => void setActive(agent)}
                      type="button"
                    >
                      {rowPending
                        ? t("actions.working")
                        : agent.is_active
                          ? t("actions.disable")
                          : t("actions.enable")}
                    </button>
                    <button
                      aria-label={t("actions.delete_label", { name: agent.name })}
                      className="button button-danger"
                      disabled={!agent.can_manage || controlsDisabled}
                      onClick={() => void remove(agent)}
                      type="button"
                    >
                      {t("actions.delete")}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {editing !== null ? (
        <div className="dialog-backdrop" onMouseDown={handleBackdrop}>
          <div
            aria-labelledby={dialogTitleId}
            aria-modal="true"
            className="dialog-panel agent-dialog"
            onKeyDown={handleDialogKeyDown}
            ref={dialogRef}
            role="dialog"
          >
            <div className="dialog-heading">
              <h2 id={dialogTitleId}>{dialogTitle}</h2>
            </div>
            {dialogError ? (
              <p className="form-error" role="alert">
                {dialogError}
              </p>
            ) : null}
            <AgentForm
              agent={editing === "new" ? null : editing}
              collections={collections}
              models={models}
              onCancel={closeEditor}
              onSubmit={save}
              saving={saving}
              workspaces={workspaces}
            />
          </div>
        </div>
      ) : null}
    </section>
  );
}
