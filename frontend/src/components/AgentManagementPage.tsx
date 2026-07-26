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
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { useTranslation } from "@/hooks/useTranslation";
import { ApiError } from "@/lib/api-error";
import { MAX_RESOURCE_NAME_LENGTH } from "@/lib/limits";
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
};

type AgentTab = "personal" | "global";

const FOCUSABLE_SELECTOR =
  "button, input:not([type='hidden']), select, textarea, [tabindex]:not([tabindex='-1'])";

function focusableElements(container: HTMLElement | null) {
  return Array.from(
    container?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [],
  ).filter((element) => !element.matches(":disabled"));
}

export function AgentManagementPage({
  initialCreate = false,
}: AgentManagementPageProps) {
  const router = useRouter();
  const { t } = useTranslation("agents");
  const { user } = useCurrentUser();
  const dialogTitleId = useId();
  const [tab, setTab] = useState<AgentTab>("personal");
  const [agents, setAgents] = useState<AgentResource[]>([]);
  const [workspaces, setWorkspaces] = useState<WorkspaceResource[]>([]);
  const [collections, setCollections] = useState<KnowledgeCollectionResource[]>([]);
  const [models, setModels] = useState<ModelListResponse | null>(null);
  const [editing, setEditing] = useState<AgentResource | "new" | null>(() =>
    initialCreate ? "new" : null,
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
  const initialCreateRouteRef = useRef(initialCreate);

  const load = useCallback(
    async (showLoading = true) => {
      const generation = ++loadGenerationRef.current;
      if (mountedRef.current && showLoading) {
        setLoadState("loading");
      }
      try {
        // Public agents are genuinely shared definitions, so both kinds load
        // together and the tab only decides which slice is on screen.
        const [ownedResult, globalResult, workspaceResult, collectionResult, modelResult] =
          await Promise.all([
            listAgents("owned", { includeInactive: true }),
            listAgents("global"),
            listWorkspaces("visible"),
            listKnowledgeCollections("visible"),
            getModels(),
          ]);
        if (!mountedRef.current || loadGenerationRef.current !== generation) {
          return false;
        }
        const seen = new Set<string>();
        setAgents(
          [...ownedResult.agents, ...globalResult.agents].filter((agent) => {
            if (seen.has(agent.id)) {
              return false;
            }
            seen.add(agent.id);
            return true;
          }),
        );
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
    [t],
  );

  useEffect(() => {
    const lifecycleGeneration = ++lifecycleGenerationRef.current;
    mountedRef.current = true;
    activeMutationRef.current = null;
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
  }, [load]);

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
    if (initialCreateRouteRef.current) {
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
        createdHome = await createWorkspace(
          tab === "global" ? "global" : "private",
          submission.workspace,
        );
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
        if (tab === "global") {
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

  /**
   * Fork a visible agent into a private one the caller owns.
   *
   * References are carried over verbatim rather than duplicated: a public
   * knowledge base is genuinely shared, and keeping `home_workspace_id` means
   * the copy keeps reading the files the caller already accumulated under that
   * Workspace. Minting a new Workspace here would strand them.
   */
  async function copyAgent(agent: AgentResource) {
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
      const copy = await createAgent({
        name: t("actions.copy_name", { name: agent.name }).slice(
          0,
          MAX_RESOURCE_NAME_LENGTH,
        ),
        config: agent.config,
      });
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setTab("personal");
        await load(false);
        if (mountedRef.current) {
          setPageError(null);
        }
        return copy;
      }
    } catch (error) {
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setPageError(
          error instanceof ApiError && error.code === "resource_name_taken"
            ? t("errors.copy_name_taken")
            : t("errors.copy_failed"),
        );
      }
    } finally {
      const shouldUpdate = isCurrentMutation(mutation, lifecycleGeneration);
      finishMutation(mutation);
      if (shouldUpdate) {
        setPendingAgentId(null);
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
  const isAdmin = user?.role === "admin";
  const visibleAgents = agents.filter((agent) =>
    tab === "personal" ? agent.scope === "private" : agent.scope === "global",
  );
  // Publishing is an admin action; using and copying a public agent is not.
  const canCreateHere = tab === "personal" || isAdmin;

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
              {tab === "global" ? t("global.title") : t("personal.title")}
            </h1>
            <p>{tab === "global" ? t("global.summary") : t("personal.summary")}</p>
          </div>
          {canCreateHere ? (
            <button
              className="button button-primary"
              disabled={controlsDisabled}
              onClick={(event) => openEditor("new", event.currentTarget)}
              type="button"
            >
              {tab === "global" ? t("actions.create_global") : t("actions.create")}
            </button>
          ) : null}
        </header>

        <div className="resource-tabs" role="tablist">
          <button
            aria-selected={tab === "personal"}
            data-active={tab === "personal"}
            disabled={controlsDisabled}
            onClick={() => setTab("personal")}
            role="tab"
            type="button"
          >
            {t("tabs.personal")}
          </button>
          <button
            aria-selected={tab === "global"}
            data-active={tab === "global"}
            disabled={controlsDisabled}
            onClick={() => setTab("global")}
            role="tab"
            type="button"
          >
            {t("tabs.global")}
          </button>
        </div>

        {pageError ? (
          <p className="form-error" role="alert">
            {pageError}
          </p>
        ) : null}

        {visibleAgents.length === 0 ? (
          <p className="empty-state">{t("states.empty")}</p>
        ) : (
          <ul className="management-list">
            {visibleAgents.map((agent) => {
              const rowPending = pendingAgentId === agent.id;
              return (
                <li key={agent.id}>
                  <div className="management-list__summary">
                    <strong>{agent.name}</strong>
                    <span>{agent.is_active ? t("states.active") : t("states.inactive")}</span>
                  </div>
                  <div className="management-list__actions">
                    <button
                      aria-label={t("actions.copy_label", { name: agent.name })}
                      className="button"
                      disabled={controlsDisabled}
                      onClick={() => void copyAgent(agent)}
                      type="button"
                    >
                      {t("actions.copy")}
                    </button>
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
