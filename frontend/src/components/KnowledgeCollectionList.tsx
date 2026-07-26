"use client";

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import { KnowledgeCollectionForm } from "@/components/KnowledgeCollectionForm";
import { KnowledgeDocumentTable } from "@/components/KnowledgeDocumentTable";
import { KnowledgeUploader } from "@/components/KnowledgeUploader";
import { ResourceDetailHeader } from "@/components/resource/ResourceDetailHeader";
import { ResourceSidebar } from "@/components/resource/ResourceSidebar";
import { ResourceSidebarLayout } from "@/components/resource/ResourceSidebarLayout";
import { useResourceSidebarSelection } from "@/components/resource/useResourceSidebarSelection";
import { useTranslation } from "@/hooks/useTranslation";
import {
  deleteKnowledgeCollection,
  listKnowledgeCollections,
  updateKnowledgeCollection,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { KnowledgeCollection, ResourceScope } from "@/lib/types";

export type KnowledgeCollectionListProps = {
  scope: ResourceScope;
};

type EditingCollection = KnowledgeCollection | "new" | null;
type CollectionRow = { collection: KnowledgeCollection; manageable: boolean };

function dedupeCollections(...groups: KnowledgeCollection[][]): KnowledgeCollection[] {
  const result = new Map<string, KnowledgeCollection>();
  for (const collection of groups.flat()) {
    if (!result.has(collection.id)) {
      result.set(collection.id, collection);
    }
  }
  return [...result.values()];
}

export function KnowledgeCollectionList({ scope }: KnowledgeCollectionListProps) {
  const { t } = useTranslation("knowledge");
  const titleId = useId();
  const editorTitleId = useId();
  const [ownedCollections, setOwnedCollections] = useState<KnowledgeCollection[]>([]);
  const [sharedCollections, setSharedCollections] = useState<KnowledgeCollection[]>([]);
  const [editing, setEditing] = useState<EditingCollection>(null);
  const [loadedScope, setLoadedScope] = useState<ResourceScope | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [formBusy, setFormBusy] = useState(false);
  const [pendingCollectionId, setPendingCollectionId] = useState<string | null>(null);
  const [pageErrorKey, setPageErrorKey] = useState<string | null>(null);
  const [documentVersion, setDocumentVersion] = useState(0);
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
            listKnowledgeCollections("owned", { includeInactive: true }),
            listKnowledgeCollections("global"),
          ]);
          if (!mountedRef.current || loadGenerationRef.current !== generation) {
            return false;
          }
          const collections = dedupeCollections(
            ownedResult.collections,
            globalResult.collections,
          );
          setOwnedCollections(
            collections.filter((collection) => collection.scope === "private"),
          );
          setSharedCollections(
            collections.filter((collection) => collection.scope === "global"),
          );
        } else {
          const result = await listKnowledgeCollections("global", {
            includeInactive: true,
          });
          if (!mountedRef.current || loadGenerationRef.current !== generation) {
            return false;
          }
          setOwnedCollections(
            dedupeCollections(result.collections).filter(
              (collection) => collection.scope === "global",
            ),
          );
          setSharedCollections([]);
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
    setPendingCollectionId(null);
    setPageErrorKey(null);
    setDocumentVersion(0);
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

  function canManageDefinition(collection: KnowledgeCollection) {
    return collection.can_manage && collection.scope === scope;
  }

  function openEditor(target: Exclude<EditingCollection, null>) {
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

  async function setActive(collection: KnowledgeCollection) {
    if (!canManageDefinition(collection)) {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    setPendingCollectionId(collection.id);
    setPageErrorKey(null);
    try {
      await updateKnowledgeCollection(scope, collection.id, {
        name: collection.name,
        config: collection.config,
        is_active: !collection.is_active,
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
        setPendingCollectionId(null);
      }
    }
  }

  async function remove(collection: KnowledgeCollection) {
    if (
      !canManageDefinition(collection) ||
      activeMutationRef.current !== null ||
      formBusy ||
      editing !== null ||
      !window.confirm(t("actions.deleteConfirm", { name: collection.name }))
    ) {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    setPendingCollectionId(collection.id);
    setPageErrorKey(null);
    try {
      await deleteKnowledgeCollection(scope, collection.id);
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
        setPendingCollectionId(null);
      }
    }
  }

  const rows: CollectionRow[] = useMemo(
    () => [
      ...ownedCollections.map((collection) => ({ collection, manageable: true })),
      ...sharedCollections.map((collection) => ({ collection, manageable: false })),
    ],
    [ownedCollections, sharedCollections],
  );
  const items = useMemo(
    () =>
      rows.map((row) => ({
        id: row.collection.id,
        name: row.collection.name,
        scope: row.collection.scope,
        isActive: row.collection.is_active,
        manageable: row.manageable,
      })),
    [rows],
  );
  const selection = useResourceSidebarSelection(items, scope);
  const selectedRow =
    rows.find((row) => row.collection.id === selection.selectedItem?.id) ?? null;

  if (
    loadState === "loading" ||
    (loadState === "ready" && loadedScope !== scope)
  ) {
    return (
      <section className="management-page">
        <p className="knowledge-state" role="status">
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
    pendingCollectionId !== null || formBusy || editing !== null;
  const pageTitle = scope === "global" ? t("global.title") : t("personal.title");
  const editorTitle =
    editing === "new"
      ? scope === "global"
        ? t("editor.createGlobalTitle")
        : t("editor.createTitle")
      : editing === null
        ? ""
        : t("editor.editTitle", { name: editing.name });

  const selectedCanManage = selectedRow
    ? selectedRow.manageable && canManageDefinition(selectedRow.collection)
    : false;
  const selectedRowPending =
    selectedRow !== null && pendingCollectionId === selectedRow.collection.id;
  const tabLabel =
    selection.tab === "personal" ? t("sidebar.personalTab") : t("sidebar.globalTab");

  return (
    <ResourceSidebarLayout
      scope={scope}
      sidebar={
        <ResourceSidebar
          createDisabled={controlsDisabled}
          labels={{
            activeBadge: t("states.active"),
            collapse: t("sidebar.collapse"),
            create: scope === "global" ? t("actions.createGlobal") : t("actions.create"),
            empty: t("states.empty"),
            expand: t("sidebar.expand"),
            globalTab: t("sidebar.globalTab"),
            inactiveBadge: t("states.inactive"),
            noResults: t("states.noResults"),
            openItem: (name) => t("sidebar.openLabel", { name }),
            personalTab: t("sidebar.personalTab"),
            searchLabel: t("sidebar.searchLabel"),
            searchPlaceholder: t("sidebar.searchPlaceholder"),
          }}
          onCreate={() => openEditor("new")}
          selection={selection}
          title={pageTitle}
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
          <KnowledgeCollectionForm
            collection={editing === "new" ? null : editing}
            onCancel={closeEditor}
            onSaved={handleSaved}
            onSavingChange={setFormBusy}
            scope={scope}
          />
        </section>
      ) : null}

      {pageErrorKey ? (
        <p className="form-error" role="alert">
          {t(pageErrorKey)}
        </p>
      ) : null}

      {selectedRow ? (
        <>
          <ResourceDetailHeader
            actions={
              selectedCanManage ? (
                <>
                  <button
                    aria-label={
                      selectedRow.collection.is_active
                        ? t("actions.disableLabel", { name: selectedRow.collection.name })
                        : t("actions.enableLabel", { name: selectedRow.collection.name })
                    }
                    className="button"
                    disabled={controlsDisabled}
                    onClick={() => void setActive(selectedRow.collection)}
                    type="button"
                  >
                    {selectedRowPending
                      ? t("actions.working")
                      : selectedRow.collection.is_active
                        ? t("actions.disable")
                        : t("actions.enable")}
                  </button>
                  <button
                    aria-label={t("actions.deleteLabel", { name: selectedRow.collection.name })}
                    className="button button-danger"
                    disabled={controlsDisabled}
                    onClick={() => void remove(selectedRow.collection)}
                    type="button"
                  >
                    {t("actions.deleteDefinition")}
                  </button>
                </>
              ) : null
            }
            breadcrumb={tabLabel}
            editDisabled={controlsDisabled}
            editLabel={t("actions.editLabel", { name: selectedRow.collection.name })}
            editText={t("actions.edit")}
            name={selectedRow.collection.name}
            onEdit={
              selectedCanManage ? () => openEditor(selectedRow.collection) : undefined
            }
            statusBadge={
              selectedRow.collection.is_active ? t("states.active") : t("states.inactive")
            }
          />

          {selectedCanManage ? (
            <KnowledgeUploader
              collectionId={selectedRow.collection.id}
              disabled={controlsDisabled}
              onUploaded={() => setDocumentVersion((current) => current + 1)}
            />
          ) : null}

          <KnowledgeDocumentTable
            canManage={selectedCanManage}
            collectionId={selectedRow.collection.id}
            key={`${selectedRow.collection.id}:${documentVersion}`}
          />
        </>
      ) : (
        <p className="empty-state">{t("sidebar.mainEmpty")}</p>
      )}
    </ResourceSidebarLayout>
  );
}
