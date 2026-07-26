"use client";

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import { ResourceDetailHeader } from "@/components/resource/ResourceDetailHeader";
import { ResourceSidebar } from "@/components/resource/ResourceSidebar";
import { ResourceSidebarLayout } from "@/components/resource/ResourceSidebarLayout";
import { useResourceSidebarSelection } from "@/components/resource/useResourceSidebarSelection";
import { WorkspaceBrowser } from "@/components/WorkspaceBrowser";
import { WorkspaceForm } from "@/components/WorkspaceForm";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { useTranslation } from "@/hooks/useTranslation";
import {
  deleteWorkspace,
  listWorkspaces,
  updateWorkspace,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { Workspace, WorkspaceScope } from "@/lib/types";

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

export function WorkspaceList() {
  const { t } = useTranslation("workspaces");
  const { user } = useCurrentUser();
  const titleId = useId();
  const editorTitleId = useId();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [editing, setEditing] = useState<EditingWorkspace>(null);
  const [createScope, setCreateScope] = useState<WorkspaceScope>("private");
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
        // A public Workspace is a template every account instantiates under its
        // own prefix, so both kinds belong in one list rather than behind tabs.
        const [ownedResult, globalResult] = await Promise.all([
          listWorkspaces("owned", { includeInactive: true }),
          listWorkspaces("global"),
        ]);
        if (!mountedRef.current || loadGenerationRef.current !== generation) {
          return false;
        }
        setWorkspaces(
          dedupeWorkspaces(ownedResult.workspaces, globalResult.workspaces),
        );
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
    [],
  );

  useEffect(() => {
    const lifecycleGeneration = ++lifecycleGenerationRef.current;
    mountedRef.current = true;
    activeMutationRef.current = null;
    setEditing(null);
    setCreateScope("private");
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

  // Editing the definition is the only thing scope governs; file access never is.
  function canManageDefinition(workspace: Workspace) {
    return workspace.can_manage;
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
      await updateWorkspace(workspace.scope, workspace.id, {
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
      await deleteWorkspace(workspace.scope, workspace.id);
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

  const items = useMemo(
    () =>
      workspaces.map((workspace) => ({
        id: workspace.id,
        name: workspace.name,
        scope: workspace.scope,
        isActive: workspace.is_active,
        manageable: workspace.can_manage,
      })),
    [workspaces],
  );
  const selection = useResourceSidebarSelection(items, {
    resetKey: "workspaces",
    tabs: false,
  });
  const selectedWorkspace =
    workspaces.find((workspace) => workspace.id === selection.selectedItem?.id) ?? null;

  if (loadState === "loading") {
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
  const canPublish = user?.role === "admin";
  const editorTitle =
    editing === "new"
      ? t("managementEditor.createTitle")
      : editing === null
        ? ""
        : t("managementEditor.editTitle", { name: editing.name });

  const selectedCanManage =
    selectedWorkspace !== null && canManageDefinition(selectedWorkspace);
  const selectedIsPublic = selectedWorkspace?.scope === "global";
  const selectedPending =
    selectedWorkspace !== null && pendingWorkspaceId === selectedWorkspace.id;
  const editingScope: WorkspaceScope =
    editing === "new" ? createScope : (editing?.scope ?? "private");

  return (
    <ResourceSidebarLayout
      sidebar={
        <ResourceSidebar
          createDisabled={controlsDisabled}
          labels={{
            activeBadge: t("states.active"),
            collapse: t("sidebar.collapse"),
            create: t("actions.create"),
            empty: t("states.empty"),
            expand: t("sidebar.expand"),
            globalTab: t("sidebar.globalTab"),
            inactiveBadge: t("states.inactive"),
            noResults: t("states.noResults"),
            openItem: (name) => t("sidebar.openLabel", { name }),
            personalTab: t("sidebar.personalTab"),
            publicBadge: t("sidebar.globalTab"),
            searchLabel: t("sidebar.searchLabel"),
            searchPlaceholder: t("sidebar.searchPlaceholder"),
          }}
          onCreate={() => openEditor("new")}
          selection={selection}
          title={t("personal.title")}
          titleId={titleId}
        />
      }
      titleId={titleId}
    >
      {editing !== null ? (
        <section className="management-editor tool-panel" aria-labelledby={editorTitleId}>
          <div className="section-heading">
            <h2 id={editorTitleId}>{editorTitle}</h2>
          </div>
          <WorkspaceForm
            onCancel={closeEditor}
            onSaved={handleSaved}
            onSavingChange={setFormBusy}
            onScopeChange={editing === "new" ? setCreateScope : undefined}
            scope={editingScope}
            scopeEditable={editing === "new" && canPublish}
            workspace={editing === "new" ? null : editing}
          />
        </section>
      ) : null}

      {pageErrorKey ? (
        <p className="form-error" role="alert">
          {t(pageErrorKey)}
        </p>
      ) : null}

      {selectedWorkspace ? (
        <>
          <ResourceDetailHeader
            actions={
              selectedCanManage ? (
                <>
                  <button
                    aria-label={
                      selectedWorkspace.is_active
                        ? t("actions.disableLabel", { name: selectedWorkspace.name })
                        : t("actions.enableLabel", { name: selectedWorkspace.name })
                    }
                    className="button"
                    disabled={controlsDisabled}
                    onClick={() => void setActive(selectedWorkspace)}
                    type="button"
                  >
                    {selectedPending
                      ? t("actions.working")
                      : selectedWorkspace.is_active
                        ? t("actions.disable")
                        : t("actions.enable")}
                  </button>
                  <button
                    aria-label={t("actions.deleteLabel", { name: selectedWorkspace.name })}
                    className="button button-danger"
                    disabled={controlsDisabled}
                    onClick={() => void remove(selectedWorkspace)}
                    type="button"
                  >
                    {t("actions.delete")}
                  </button>
                </>
              ) : null
            }
            breadcrumb={
              selectedIsPublic ? t("sidebar.globalTab") : t("sidebar.personalTab")
            }
            editDisabled={controlsDisabled}
            editLabel={t("actions.editLabel", { name: selectedWorkspace.name })}
            editText={t("actions.edit")}
            name={selectedWorkspace.name}
            onEdit={
              selectedCanManage ? () => openEditor(selectedWorkspace) : undefined
            }
            statusBadge={
              selectedWorkspace.is_active ? t("states.active") : t("states.inactive")
            }
          />

          {selectedIsPublic ? (
            <p className="form-hint resource-scope-note">{t("detail.publicTemplateNote")}</p>
          ) : null}

          <WorkspaceBrowser workspaceId={selectedWorkspace.id} />
        </>
      ) : (
        <p className="empty-state">{t("sidebar.mainEmpty")}</p>
      )}
    </ResourceSidebarLayout>
  );
}
