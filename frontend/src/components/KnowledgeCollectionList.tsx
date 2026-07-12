"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import { KnowledgeCollectionForm } from "@/components/KnowledgeCollectionForm";
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
  const pageSummary =
    scope === "global" ? t("global.summary") : t("personal.summary");
  const editorTitle =
    editing === "new"
      ? scope === "global"
        ? t("editor.createGlobalTitle")
        : t("editor.createTitle")
      : editing === null
        ? ""
        : t("editor.editTitle", { name: editing.name });

  function renderRows(collections: KnowledgeCollection[], manageable: boolean) {
    if (collections.length === 0) {
      return <p className="empty-state">{t("states.empty")}</p>;
    }
    return (
      <ul className="management-list">
        {collections.map((collection) => {
          const canManage = manageable && canManageDefinition(collection);
          const rowPending = pendingCollectionId === collection.id;
          return (
            <li key={collection.id}>
              <div className="management-list__summary">
                <strong>{collection.name}</strong>
                <span>
                  {collection.is_active ? t("states.active") : t("states.inactive")}
                </span>
              </div>
              <div className="management-list__actions">
                {collection.is_active ? (
                  controlsDisabled ? (
                    <button
                      aria-label={t("actions.openDocumentsLabel", {
                        name: collection.name,
                      })}
                      className="button"
                      disabled
                      type="button"
                    >
                      {t("actions.openDocuments")}
                    </button>
                  ) : (
                    <Link
                      aria-label={t("actions.openDocumentsLabel", {
                        name: collection.name,
                      })}
                      className="button"
                      href={`/knowledge/${encodeURIComponent(collection.id)}`}
                    >
                      {t("actions.openDocuments")}
                    </Link>
                  )
                ) : null}
                {canManage ? (
                  <>
                    <button
                      aria-label={t("actions.editLabel", { name: collection.name })}
                      className="button"
                      disabled={controlsDisabled}
                      onClick={() => openEditor(collection)}
                      type="button"
                    >
                      {t("actions.edit")}
                    </button>
                    <button
                      aria-label={
                        collection.is_active
                          ? t("actions.disableLabel", { name: collection.name })
                          : t("actions.enableLabel", { name: collection.name })
                      }
                      className="button"
                      disabled={controlsDisabled}
                      onClick={() => void setActive(collection)}
                      type="button"
                    >
                      {rowPending
                        ? t("actions.working")
                        : collection.is_active
                          ? t("actions.disable")
                          : t("actions.enable")}
                    </button>
                    <button
                      aria-label={t("actions.deleteLabel", { name: collection.name })}
                      className="button button-danger"
                      disabled={controlsDisabled}
                      onClick={() => void remove(collection)}
                      type="button"
                    >
                      {t("actions.deleteDefinition")}
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
            {renderRows(ownedCollections, true)}
          </section>

          {scope === "private" ? (
            <section className="management-section" aria-labelledby={`${titleId}-shared`}>
              <div className="section-heading">
                <div>
                  <h2 id={`${titleId}-shared`}>{t("personal.sharedTitle")}</h2>
                  <p className="page-summary">{t("personal.sharedSummary")}</p>
                </div>
              </div>
              {renderRows(sharedCollections, false)}
            </section>
          ) : null}
        </div>
      </div>
    </section>
  );
}
