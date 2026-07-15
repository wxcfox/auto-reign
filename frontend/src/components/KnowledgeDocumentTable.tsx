"use client";

import { Download, Eye, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import {
  deleteKnowledgeDocument,
  downloadKnowledgeDocument,
  listKnowledgeDocuments,
  readKnowledgeDocumentContent,
  reindexKnowledgeDocument,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { KnowledgeDocument } from "@/lib/types";

export type KnowledgeDocumentTableProps = {
  canManage?: boolean;
  collectionId: string;
  documents?: KnowledgeDocument[];
  onChanged?: () => void;
};

type CleanupState = "complete" | "pending";

function visibleDocuments(documents: KnowledgeDocument[]): KnowledgeDocument[] {
  return documents.filter(
    (document) =>
      document.is_active ||
      document.error_code === "knowledge_cleanup_pending" ||
      document.error_code === "knowledge_cleanup_failed",
  );
}

export function KnowledgeDocumentTable(props: KnowledgeDocumentTableProps) {
  return (
    <KnowledgeDocumentTableInstance
      key={`${props.collectionId}\u0000${props.canManage === true}`}
      {...props}
    />
  );
}

function KnowledgeDocumentTableInstance({
  canManage = false,
  collectionId,
  documents,
  onChanged,
}: KnowledgeDocumentTableProps) {
  const { t } = useTranslation("knowledge");
  const [items, setItems] = useState<KnowledgeDocument[]>(
    visibleDocuments(documents ?? []),
  );
  const [loading, setLoading] = useState(documents === undefined);
  const [loadError, setLoadError] = useState(false);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});
  const [cleanupStates, setCleanupStates] = useState<Record<string, CleanupState>>({});
  const [previewDocument, setPreviewDocument] = useState<KnowledgeDocument | null>(null);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(false);

  useEffect(() => {
    if (documents !== undefined) {
      setItems(visibleDocuments(documents));
      setLoading(false);
      setLoadError(false);
      return;
    }

    let active = true;
    setLoading(true);
    setLoadError(false);
    listKnowledgeDocuments(collectionId, { includeInactive: canManage })
      .then((response) => {
        if (active) {
          setItems(visibleDocuments(response.documents));
        }
      })
      .catch(() => {
        if (active) {
          setItems([]);
          setLoadError(true);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [canManage, collectionId, documents, reloadVersion]);

  function clearActionError(documentId: string) {
    setActionErrors((current) => {
      if (!(documentId in current)) {
        return current;
      }
      const next = { ...current };
      delete next[documentId];
      return next;
    });
  }

  function setOperationError(documentId: string, error: unknown, fallbackKey: string) {
    const message =
      error instanceof ApiError && error.code === "resource_in_use"
        ? t("documents.resourceInUse")
        : t(fallbackKey);
    setActionErrors((current) => ({ ...current, [documentId]: message }));
  }

  async function handleReindex(document: KnowledgeDocument) {
    if (activeAction !== null) {
      return;
    }
    setActiveAction(document.id);
    clearActionError(document.id);
    try {
      const updated = await reindexKnowledgeDocument(collectionId, document.id);
      setItems((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      onChanged?.();
    } catch (error) {
      setOperationError(document.id, error, "documents.reindexError");
    } finally {
      setActiveAction(null);
    }
  }

  async function handleDelete(document: KnowledgeDocument) {
    if (activeAction !== null) {
      return;
    }
    setActiveAction(document.id);
    clearActionError(document.id);
    try {
      const result = await deleteKnowledgeDocument(collectionId, document.id);
      setCleanupStates((current) => ({
        ...current,
        [document.id]: result === null ? "complete" : "pending",
      }));
    } catch (error) {
      setOperationError(document.id, error, "documents.deleteError");
    } finally {
      setActiveAction(null);
    }
  }

  async function handlePreview(document: KnowledgeDocument) {
    if (activeAction !== null) {
      return;
    }
    setActiveAction(document.id);
    setPreviewDocument(document);
    setPreviewContent(null);
    setPreviewLoading(true);
    setPreviewError(false);
    clearActionError(document.id);
    try {
      const response = await readKnowledgeDocumentContent(collectionId, document.id);
      setPreviewContent(response.content);
    } catch {
      setPreviewError(true);
    } finally {
      setPreviewLoading(false);
      setActiveAction(null);
    }
  }

  async function handleDownload(document: KnowledgeDocument) {
    if (activeAction !== null) {
      return;
    }
    setActiveAction(document.id);
    clearActionError(document.id);
    let objectUrl: string | null = null;
    try {
      const blob = await downloadKnowledgeDocument(collectionId, document.id);
      objectUrl = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = document.name;
      anchor.rel = "noopener";
      window.document.body.append(anchor);
      anchor.click();
      anchor.remove();
    } catch (error) {
      setOperationError(document.id, error, "documents.downloadError");
    } finally {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
      setActiveAction(null);
    }
  }

  if (loading) {
    return (
      <p className="knowledge-state" role="status">
        {t("documents.loading")}
      </p>
    );
  }

  if (loadError) {
    return (
      <div className="knowledge-state knowledge-state--error">
        <p role="alert">{t("documents.loadError")}</p>
        <button onClick={() => setReloadVersion((current) => current + 1)} type="button">
          {t("actions.retry")}
        </button>
      </div>
    );
  }

  return (
    <div className="knowledge-documents">
      {items.length === 0 ? (
        <p className="knowledge-state">{t("documents.empty")}</p>
      ) : (
        <div className="knowledge-document-table-wrap">
          <table className="knowledge-document-table">
            <thead>
              <tr>
                <th scope="col">{t("documents.name")}</th>
                <th scope="col">{t("documents.status")}</th>
                <th scope="col">{t("documents.size")}</th>
                <th scope="col">{t("documents.generation")}</th>
                <th scope="col">{t("documents.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((document) => {
                const cleanupState = cleanupStates[document.id];
                const cleanupPending =
                  cleanupState === "pending" ||
                  (cleanupState === undefined &&
                    document.error_code === "knowledge_cleanup_pending");
                const cleanupFailed =
                  cleanupState === undefined &&
                  document.error_code === "knowledge_cleanup_failed";
                const cleaned = cleanupState === "complete";
                const cleanupRetry = cleanupPending || cleanupFailed;
                const effectivelyActive =
                  document.is_active && cleanupState === undefined;
                return (
                  <tr key={document.id}>
                    <td>
                      <strong>{document.name}</strong>
                      <span>{document.mime_type}</span>
                      {!effectivelyActive ? (
                        <span className="knowledge-document__inactive">
                          {t("documents.inactive")}
                        </span>
                      ) : null}
                      {document.error_message ? (
                        <span className="knowledge-document__error" role="alert">
                          {document.error_message}
                        </span>
                      ) : null}
                      {cleanupPending ? (
                        <span className="knowledge-document__warning" role="status">
                          {t("documents.cleanupPending")}
                        </span>
                      ) : cleanupFailed ? (
                        <span className="knowledge-document__error" role="alert">
                          {t("documents.cleanupFailed")}
                        </span>
                      ) : cleaned ? (
                        <span className="knowledge-document__success" role="status">
                          {t("documents.cleanupComplete")}
                        </span>
                      ) : null}
                      {actionErrors[document.id] ? (
                        <span className="knowledge-document__error" role="alert">
                          {actionErrors[document.id]}
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <span className="knowledge-status" data-status={document.status}>
                        {t(`status.${document.status}`)}
                      </span>
                    </td>
                    <td>{formatBytes(document.size_bytes)}</td>
                    <td>{document.index_generation}</td>
                    <td>
                      <div className="knowledge-document-actions">
                        {document.status === "ready" && effectivelyActive ? (
                          <button
                            aria-label={t("actions.preview", { name: document.name })}
                            disabled={activeAction !== null}
                            onClick={() => void handlePreview(document)}
                            type="button"
                          >
                            <Eye aria-hidden="true" size={15} />
                          </button>
                        ) : null}
                        {effectivelyActive ? (
                          <button
                            aria-label={t("actions.download", { name: document.name })}
                            disabled={activeAction !== null}
                            onClick={() => void handleDownload(document)}
                            type="button"
                          >
                            <Download aria-hidden="true" size={15} />
                          </button>
                        ) : null}
                        {canManage && effectivelyActive ? (
                          <button
                            aria-label={t("actions.reindex", { name: document.name })}
                            disabled={activeAction !== null}
                            onClick={() => void handleReindex(document)}
                            type="button"
                          >
                            <RefreshCw aria-hidden="true" size={15} />
                          </button>
                        ) : null}
                        {canManage && !cleaned && (effectivelyActive || cleanupRetry) ? (
                          <button
                            aria-label={
                              cleanupRetry
                                ? t("actions.retryCleanup", { name: document.name })
                                : t("actions.delete", { name: document.name })
                            }
                            className="knowledge-document-action--danger"
                            disabled={activeAction !== null}
                            onClick={() => void handleDelete(document)}
                            type="button"
                          >
                            <Trash2 aria-hidden="true" size={15} />
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {previewDocument ? (
        <section
          aria-labelledby="knowledge-preview-title"
          className="knowledge-preview"
        >
          <div className="knowledge-preview__heading">
            <h3 id="knowledge-preview-title">
              {t("documents.previewTitle", { name: previewDocument.name })}
            </h3>
            <button
              aria-label={t("actions.closePreview")}
              onClick={() => setPreviewDocument(null)}
              type="button"
            >
              <X aria-hidden="true" size={16} />
            </button>
          </div>
          {previewLoading ? (
            <p role="status">{t("documents.previewLoading")}</p>
          ) : previewError ? (
            <p className="form-error" role="alert">
              {t("documents.previewError")}
            </p>
          ) : (
            <pre>{previewContent}</pre>
          )}
        </section>
      ) : null}
    </div>
  );
}

function formatBytes(size: number): string {
  if (size < 1_024) {
    return `${size} B`;
  }
  if (size < 1_048_576) {
    return `${(size / 1_024).toFixed(1)} KB`;
  }
  return `${(size / 1_048_576).toFixed(1)} MB`;
}
