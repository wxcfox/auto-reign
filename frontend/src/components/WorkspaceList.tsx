"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import { WorkspaceForm } from "@/components/WorkspaceForm";
import { useTranslation } from "@/hooks/useTranslation";
import {
  deleteWorkspace,
  listWorkspaces,
  updateWorkspace,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { Workspace, WorkspaceScope } from "@/lib/types";

export type WorkspaceListProps = {
  scope: WorkspaceScope;
};

type EditingWorkspace = Workspace | "new" | null;

function dedupeWorkspaces(...groups: Workspace[][]): Workspace[] {
  const result = new Map<string, Workspace>();
  for (const workspace of groups.flat()) {
    if (!result.has(workspace.id)) {
      result.set(workspace.id, workspace);
    }
  }
  return [...result.values()];
}

export function WorkspaceList({ scope }: WorkspaceListProps) {
  const { t } = useTranslation("workspaces");
  const titleId = useId();
  const editorTitleId = useId();
  const [ownedWorkspaces, setOwnedWorkspaces] = useState<Workspace[]>([]);
  const [sharedWorkspaces, setSharedWorkspaces] = useState<Workspace[]>([]);
  const [editing, setEditing] = useState<EditingWorkspace>(null);
  const [loadedScope, setLoadedScope] = useState<WorkspaceScope | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [formBusy, setFormBusy] = useState(false);
  const [pendingWorkspaceId, setPendingWorkspaceId] = useState<string | null>(null);
  const [pageErrorKey, setPageErrorKey] = useState<string | null>(null);
  const mountedRef = useRef(false);
  const lifecycleGenerationRef = useRef(0);
  const loadGenerationRef = useRef(0);
  const mutationSequenceRef = useRef(0);
  const activeMutationRef = useRef<number | null>(null);

  const load = useCallback(
    async (showLoading = true) => {
      const generation = ++loadGenerationRef.current;
      if (mountedRef.current && showLoading) {
        setLoadState("loading");
        setPageErrorKey(null);
      }
      try {
        if (scope === "private") {
          const [ownedResult, globalResult] = await Promise.all([
            listWorkspaces("owned", { includeInactive: true }),
            listWorkspaces("global"),
          ]);
          if (!mountedRef.current || loadGenerationRef.current !== generation) {
            return false;
          }
          const workspaces = dedupeWorkspaces(
            ownedResult.workspaces,
            globalResult.workspaces,
          );
          setOwnedWorkspaces(
            workspaces.filter((workspace) => workspace.scope === "private"),
          );
          setSharedWorkspaces(
            workspaces.filter((workspace) => workspace.scope === "global"),
          );
        } else {
          const result = await listWorkspaces("global", { includeInactive: true });
          if (!mountedRef.current || loadGenerationRef.current !== generation) {
            return false;
          }
          setOwnedWorkspaces(
            dedupeWorkspaces(result.workspaces).filter(
              (workspace) => workspace.scope === "global",
            ),
          );
          setSharedWorkspaces([]);
        }
        setLoadedScope(scope);
        setLoadState("ready");
        return true;
      } catch {
        if (mountedRef.current && loadGenerationRef.current === generation) {
          if (showLoading) {
            setLoadState("error");
          } else {
            setPageErrorKey((current) => current ?? "errors.loadFailed");
          }
        }
        return false;
      }
    },
    [scope],
  );

  useEffect(() => {
    const lifecycleGeneration = ++lifecycleGenerationRef.current;
    mountedRef.current = true;
    activeMutationRef.current = null;
    setEditing(null);
    setLoadedScope(null);
    setFormBusy(false);
    setPendingWorkspaceId(null);
    setPageErrorKey(null);
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

  function startMutation() {
    if (activeMutationRef.current !== null || formBusy || editing !== null) {
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

  function canManageDefinition(workspace: Workspace) {
    return workspace.can_manage && workspace.scope === scope;
  }

  function openEditor(target: Exclude<EditingWorkspace, null>) {
    if (activeMutationRef.current !== null || formBusy || editing !== null) {
      return;
    }
    setPageErrorKey(null);
    setEditing(target);
  }

  function closeEditor() {
    if (!formBusy) {
      setEditing(null);
    }
  }

  function handleSaved() {
    if (!mountedRef.current) {
      return;
    }
    setFormBusy(false);
    setEditing(null);
    setPageErrorKey(null);
    void load(false);
  }

  function actionErrorKey(error: unknown, fallbackKey: string) {
    return error instanceof ApiError && error.code === "resource_in_use"
      ? "errors.resourceInUse"
      : fallbackKey;
  }

  async function setActive(workspace: Workspace) {
    if (!canManageDefinition(workspace)) {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    setPendingWorkspaceId(workspace.id);
    setPageErrorKey(null);
    try {
      await updateWorkspace(scope, workspace.id, {
        name: workspace.name,
        config: workspace.config,
        is_active: !workspace.is_active,
      });
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        await load(false);
      }
    } catch (error) {
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setPageErrorKey(actionErrorKey(error, "errors.statusFailed"));
      }
    } finally {
      const shouldUpdate = isCurrentMutation(mutation, lifecycleGeneration);
      finishMutation(mutation);
      if (shouldUpdate) {
        setPendingWorkspaceId(null);
      }
    }
  }

  async function remove(workspace: Workspace) {
    if (
      !canManageDefinition(workspace) ||
      activeMutationRef.current !== null ||
      formBusy ||
      editing !== null ||
      !window.confirm(t("actions.deleteConfirm", { name: workspace.name }))
    ) {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    setPendingWorkspaceId(workspace.id);
    setPageErrorKey(null);
    try {
      await deleteWorkspace(scope, workspace.id);
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        await load(false);
      }
    } catch (error) {
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setPageErrorKey(actionErrorKey(error, "errors.deleteFailed"));
      }
    } finally {
      const shouldUpdate = isCurrentMutation(mutation, lifecycleGeneration);
      finishMutation(mutation);
      if (shouldUpdate) {
        setPendingWorkspaceId(null);
      }
    }
  }

  if (
    loadState === "loading" ||
    (loadState === "ready" && loadedScope !== scope)
  ) {
    return (
      <section className="management-page">
        <p className="workspace-state" role="status">
          {t("states.loading")}
        </p>
      </section>
    );
  }

  if (loadState === "error") {
    return (
      <section className="management-page management-load-error" role="alert">
        <p>{t("errors.loadFailed")}</p>
        <button className="button" onClick={() => void load()} type="button">
          {t("actions.retry")}
        </button>
      </section>
    );
  }

  const controlsDisabled =
    pendingWorkspaceId !== null || formBusy || editing !== null;
  const pageTitle = scope === "global" ? t("global.title") : t("personal.title");
  const pageSummary =
    scope === "global" ? t("global.summary") : t("personal.summary");
  const editorTitle =
    editing === "new"
      ? scope === "global"
        ? t("managementEditor.createGlobalTitle")
        : t("managementEditor.createTitle")
      : editing === null
        ? ""
        : t("managementEditor.editTitle", { name: editing.name });

  function renderRows(workspaces: Workspace[], manageable: boolean) {
    if (workspaces.length === 0) {
      return <p className="empty-state">{t("states.empty")}</p>;
    }
    return (
      <ul className="management-list">
        {workspaces.map((workspace) => {
          const canManage = manageable && canManageDefinition(workspace);
          const rowPending = pendingWorkspaceId === workspace.id;
          return (
            <li key={workspace.id}>
              <div className="management-list__summary">
                <strong>{workspace.name}</strong>
                <span>
                  {workspace.is_active ? t("states.active") : t("states.inactive")}
                </span>
              </div>
              <div className="management-list__actions">
                {workspace.is_active ? (
                  controlsDisabled ? (
                    <button
                      aria-label={t("actions.openFilesLabel", { name: workspace.name })}
                      className="button"
                      disabled
                      type="button"
                    >
                      {t("actions.openFiles")}
                    </button>
                  ) : (
                    <Link
                      aria-label={t("actions.openFilesLabel", { name: workspace.name })}
                      className="button"
                      href={`/workspaces/${encodeURIComponent(workspace.id)}`}
                    >
                      {t("actions.openFiles")}
                    </Link>
                  )
                ) : null}
                {canManage ? (
                  <>
                    <button
                      aria-label={t("actions.editLabel", { name: workspace.name })}
                      className="button"
                      disabled={controlsDisabled}
                      onClick={() => openEditor(workspace)}
                      type="button"
                    >
                      {t("actions.edit")}
                    </button>
                    <button
                      aria-label={
                        workspace.is_active
                          ? t("actions.disableLabel", { name: workspace.name })
                          : t("actions.enableLabel", { name: workspace.name })
                      }
                      className="button"
                      disabled={controlsDisabled}
                      onClick={() => void setActive(workspace)}
                      type="button"
                    >
                      {rowPending
                        ? t("actions.working")
                        : workspace.is_active
                          ? t("actions.disable")
                          : t("actions.enable")}
                    </button>
                    <button
                      aria-label={t("actions.deleteLabel", { name: workspace.name })}
                      className="button button-danger"
                      disabled={controlsDisabled}
                      onClick={() => void remove(workspace)}
                      type="button"
                    >
                      {t("actions.delete")}
                    </button>
                  </>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <section className="management-page" aria-labelledby={titleId} data-scope={scope}>
      <div className="management-content">
        <header className="management-header">
          <div>
            <h1 id={titleId}>{pageTitle}</h1>
            <p>{pageSummary}</p>
          </div>
          <button
            className="button button-primary"
            disabled={controlsDisabled}
            onClick={() => openEditor("new")}
            type="button"
          >
            {scope === "global"
              ? t("actions.createGlobal")
              : t("actions.create")}
          </button>
        </header>

        {editing !== null ? (
          <section className="management-editor tool-panel" aria-labelledby={editorTitleId}>
            <div className="section-heading">
              <h2 id={editorTitleId}>{editorTitle}</h2>
            </div>
            <WorkspaceForm
              onCancel={closeEditor}
              onSaved={handleSaved}
              onSavingChange={setFormBusy}
              scope={scope}
              workspace={editing === "new" ? null : editing}
            />
          </section>
        ) : null}

        {pageErrorKey ? (
          <p className="form-error" role="alert">
            {t(pageErrorKey)}
          </p>
        ) : null}

        <div className="management-sections">
          <section className="management-section" aria-labelledby={`${titleId}-owned`}>
            <div className="section-heading">
              <div>
                <h2 id={`${titleId}-owned`}>
                  {scope === "global"
                    ? t("global.listTitle")
                    : t("personal.ownedTitle")}
                </h2>
                <p className="page-summary">
                  {scope === "global"
                    ? t("global.listSummary")
                    : t("personal.ownedSummary")}
                </p>
              </div>
            </div>
            {renderRows(ownedWorkspaces, true)}
          </section>

          {scope === "private" ? (
            <section className="management-section" aria-labelledby={`${titleId}-shared`}>
              <div className="section-heading">
                <div>
                  <h2 id={`${titleId}-shared`}>{t("personal.sharedTitle")}</h2>
                  <p className="page-summary">{t("personal.sharedSummary")}</p>
                </div>
              </div>
              {renderRows(sharedWorkspaces, false)}
            </section>
          ) : null}
        </div>
      </div>
    </section>
  );
}
