"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { listKnowledgeDocuments } from "@/lib/api";
import {
  MAX_DOCUMENTS_PER_SCOPE,
  MAX_KNOWLEDGE_SCOPES,
} from "@/lib/limits";
import type { KnowledgeCollectionResource, KnowledgeDocument } from "@/lib/types";
import type { KnowledgeScopeDraft } from "./agent-form-state";

export interface KnowledgeScopeEditorProps {
  collections: KnowledgeCollectionResource[];
  value: KnowledgeScopeDraft[];
  disabled?: boolean;
  onAvailabilityChange?: (available: boolean) => void;
  onChange: (value: KnowledgeScopeDraft[]) => void;
}

export function KnowledgeScopeEditor({
  collections,
  value,
  disabled = false,
  onAvailabilityChange,
  onChange,
}: KnowledgeScopeEditorProps) {
  const { t } = useTranslation("agents");
  const [candidateId, setCandidateId] = useState("");
  const [documents, setDocuments] = useState<Record<string, KnowledgeDocument[]>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, boolean>>({});
  const mountedRef = useRef(false);
  const valueRef = useRef(value);
  const documentsRef = useRef<Record<string, KnowledgeDocument[]>>({});
  const errorsRef = useRef<Record<string, boolean>>({});
  const inFlightRef = useRef(new Map<string, Promise<void>>());
  valueRef.current = value;

  const selectedIds = useMemo(
    () => new Set(value.map((item) => item.collectionId)),
    [value],
  );
  const availableCollectionIds = useMemo(
    () => new Set(collections.map((collection) => collection.id)),
    [collections],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const loadDocuments = useCallback((collectionId: string, retry = false) => {
    const activeRequest = inFlightRef.current.get(collectionId);
    if (activeRequest !== undefined) {
      return activeRequest;
    }
    if (!retry && documentsRef.current[collectionId] !== undefined) {
      return Promise.resolve();
    }

    errorsRef.current[collectionId] = false;
    if (mountedRef.current) {
      setLoading((current) => ({ ...current, [collectionId]: true }));
      setErrors((current) => ({ ...current, [collectionId]: false }));
    }

    const request = Promise.resolve()
      .then(() => listKnowledgeDocuments(collectionId))
      .then((response) => {
        documentsRef.current[collectionId] = response.documents;
        errorsRef.current[collectionId] = false;
        if (mountedRef.current) {
          setDocuments((current) => ({
            ...current,
            [collectionId]: response.documents,
          }));
        }
      })
      .catch(() => {
        errorsRef.current[collectionId] = true;
        if (mountedRef.current) {
          setErrors((current) => ({ ...current, [collectionId]: true }));
        }
      })
      .finally(() => {
        if (inFlightRef.current.get(collectionId) === request) {
          inFlightRef.current.delete(collectionId);
        }
        if (mountedRef.current) {
          setLoading((current) => ({ ...current, [collectionId]: false }));
        }
      });
    inFlightRef.current.set(collectionId, request);
    return request;
  }, []);

  useEffect(() => {
    for (const scope of value) {
      if (
        scope.mode === "subset" &&
        availableCollectionIds.has(scope.collectionId) &&
        documentsRef.current[scope.collectionId] === undefined &&
        !errorsRef.current[scope.collectionId]
      ) {
        void loadDocuments(scope.collectionId);
      }
    }
  }, [availableCollectionIds, loadDocuments, value]);

  const available = useMemo(
    () =>
      value.every((scope) => {
        if (!availableCollectionIds.has(scope.collectionId)) {
          return false;
        }
        if (scope.mode === "all") {
          return true;
        }
        const loadedDocuments = documents[scope.collectionId];
        if (
          loadedDocuments === undefined ||
          loading[scope.collectionId] ||
          errors[scope.collectionId]
        ) {
          return false;
        }
        const activeDocumentIds = new Set(
          loadedDocuments
            .filter((document) => document.is_active)
            .map((document) => document.id),
        );
        return scope.documentIds.every((documentId) =>
          activeDocumentIds.has(documentId),
        );
      }),
    [availableCollectionIds, documents, errors, loading, value],
  );

  useEffect(() => {
    onAvailabilityChange?.(available);
  }, [available, onAvailabilityChange, value]);

  function emit(next: KnowledgeScopeDraft[]) {
    valueRef.current = next;
    onChange(next);
  }

  function replaceScope(
    collectionId: string,
    update: (scope: KnowledgeScopeDraft) => KnowledgeScopeDraft,
  ) {
    const current = valueRef.current;
    const index = current.findIndex((scope) => scope.collectionId === collectionId);
    if (index < 0) {
      return;
    }
    const updated = update(current[index]);
    if (updated === current[index]) {
      return;
    }
    emit(current.map((scope, currentIndex) => (currentIndex === index ? updated : scope)));
  }

  function addCandidate() {
    const current = valueRef.current;
    if (
      !candidateId ||
      current.length >= MAX_KNOWLEDGE_SCOPES ||
      current.some((scope) => scope.collectionId === candidateId) ||
      !collections.some((collection) => collection.id === candidateId)
    ) {
      return;
    }
    emit([
      ...current,
      { collectionId: candidateId, mode: "all", documentIds: [] },
    ]);
    setCandidateId("");
  }

  function toggleDocument(collectionId: string, documentId: string, checked: boolean) {
    replaceScope(collectionId, (scope) => {
      const documentIds = [...new Set(scope.documentIds)];
      if (checked) {
        if (
          documentIds.includes(documentId) ||
          documentIds.length >= MAX_DOCUMENTS_PER_SCOPE
        ) {
          return scope;
        }
        return { ...scope, documentIds: [...documentIds, documentId] };
      }
      return {
        ...scope,
        documentIds: documentIds.filter((id) => id !== documentId),
      };
    });
  }

  const candidateInvalid =
    !candidateId ||
    value.length >= MAX_KNOWLEDGE_SCOPES ||
    selectedIds.has(candidateId) ||
    !collections.some((collection) => collection.id === candidateId);

  return (
    <fieldset className="agent-form-section knowledge-scope-editor" disabled={disabled}>
      <legend>{t("knowledge.title")}</legend>
      <div className="knowledge-scope-add">
        <select
          aria-label={t("knowledge.add_collection")}
          onChange={(event) => setCandidateId(event.target.value)}
          value={candidateId}
        >
          <option value="">{t("knowledge.select_collection")}</option>
          {collections
            .filter((collection) => !selectedIds.has(collection.id))
            .map((collection) => (
              <option key={collection.id} value={collection.id}>
                {collection.name}
              </option>
            ))}
        </select>
        <button
          className="button"
          disabled={candidateInvalid}
          onClick={addCandidate}
          type="button"
        >
          {t("knowledge.add_scope")}
        </button>
      </div>

      {value.map((scope) => {
        const collection = collections.find((item) => item.id === scope.collectionId);
        const selectedDocumentIds = new Set(scope.documentIds);
        const selectionFull = selectedDocumentIds.size >= MAX_DOCUMENTS_PER_SCOPE;
        const loadedDocuments = documents[scope.collectionId];
        const activeDocuments = loadedDocuments?.filter(
          (document) => document.is_active,
        );
        const activeDocumentIds = new Set(
          activeDocuments?.map((document) => document.id) ?? [],
        );
        const unavailableSelectedDocumentIds =
          loadedDocuments === undefined
            ? []
            : [...selectedDocumentIds].filter(
                (documentId) => !activeDocumentIds.has(documentId),
              );
        return (
          <section
            aria-label={collection?.name ?? scope.collectionId}
            className="knowledge-scope-card"
            key={scope.collectionId}
          >
            <div className="knowledge-scope-heading">
              <strong>{collection?.name ?? scope.collectionId}</strong>
              <button
                className="button"
                onClick={() =>
                  emit(
                    valueRef.current.filter(
                      (item) => item.collectionId !== scope.collectionId,
                    ),
                  )
                }
                type="button"
              >
                {t("actions.remove")}
              </button>
            </div>
            {collection === undefined ? (
              <p
                aria-label={t("knowledge.collection_unavailable_label")}
                className="form-error"
                role="alert"
              >
                {t("knowledge.collection_unavailable", { id: scope.collectionId })}
              </p>
            ) : null}
            <label>
              <input
                checked={scope.mode === "all"}
                name={`scope-mode-${scope.collectionId}`}
                onChange={() =>
                  replaceScope(scope.collectionId, (current) => ({
                    ...current,
                    mode: "all",
                    documentIds: [],
                  }))
                }
                type="radio"
              />
              {t("knowledge.entire_collection")}
            </label>
            <label>
              <input
                checked={scope.mode === "subset"}
                name={`scope-mode-${scope.collectionId}`}
                onChange={() =>
                  replaceScope(scope.collectionId, (current) => ({
                    ...current,
                    mode: "subset",
                    documentIds: [],
                  }))
                }
                type="radio"
              />
              {t("knowledge.selected_documents")}
            </label>

            {collection !== undefined &&
            scope.mode === "subset" &&
            loading[scope.collectionId] ? (
              <p className="agent-form-hint" role="status">
                {t("knowledge.loading_documents")}
              </p>
            ) : null}
            {collection !== undefined &&
            scope.mode === "subset" &&
            errors[scope.collectionId] ? (
              <div className="knowledge-scope-error" role="alert">
                <span>{t("knowledge.documents_load_failed")}</span>
                <button
                  className="button"
                  onClick={() => void loadDocuments(scope.collectionId, true)}
                  type="button"
                >
                  {t("actions.retry")}
                </button>
              </div>
            ) : null}
            {collection !== undefined &&
            scope.mode === "subset" &&
            activeDocuments?.length === 0 ? (
              <p className="agent-form-hint">{t("knowledge.no_documents")}</p>
            ) : null}
            {collection !== undefined && scope.mode === "subset"
              ? unavailableSelectedDocumentIds.map((documentId) => (
                  <label
                    className="knowledge-document-option knowledge-document-option--unavailable"
                    key={documentId}
                  >
                    <input
                      aria-label={t("knowledge.document_unavailable_checkbox", {
                        id: documentId,
                      })}
                      checked
                      onChange={(event) =>
                        toggleDocument(
                          scope.collectionId,
                          documentId,
                          event.target.checked,
                        )
                      }
                      type="checkbox"
                    />
                    <span>
                      <span
                        aria-label={t("knowledge.document_unavailable_alert_label", {
                          id: documentId,
                        })}
                        className="knowledge-document-unavailable-message"
                        role="alert"
                      >
                        {t("knowledge.document_unavailable", { id: documentId })}
                      </span>
                      <small>{t("knowledge.document_unavailable_hint")}</small>
                    </span>
                  </label>
                ))
              : null}
            {collection !== undefined && scope.mode === "subset"
              ? activeDocuments?.map((document) => {
                  const checked = selectedDocumentIds.has(document.id);
                  return (
                    <label className="knowledge-document-option" key={document.id}>
                      <input
                        aria-label={document.name}
                        checked={checked}
                        disabled={!checked && selectionFull}
                        onChange={(event) =>
                          toggleDocument(
                            scope.collectionId,
                            document.id,
                            event.target.checked,
                          )
                        }
                        type="checkbox"
                      />
                      <span>
                        <span>{document.name}</span>
                        <small>
                          {t("knowledge.document_status", {
                            status: t(`knowledge.status.${document.status}`),
                          })}
                        </small>
                      </span>
                    </label>
                  );
                })
              : null}
          </section>
        );
      })}
    </fieldset>
  );
}
