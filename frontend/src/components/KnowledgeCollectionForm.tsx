"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { useTranslation } from "@/hooks/useTranslation";
import {
  createKnowledgeCollection,
  updateKnowledgeCollection,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import {
  DEFAULT_KNOWLEDGE_COLLECTION_CONFIG,
  KNOWLEDGE_COLLECTION_LIMITS,
  MAX_KNOWLEDGE_COLLECTION_NAME_LENGTH,
} from "@/lib/limits";
import type {
  KnowledgeCollection,
  KnowledgeCollectionConfig,
  KnowledgeRetrievalMode,
  KnowledgeRetrieverType,
  ResourceScope,
} from "@/lib/types";

export { DEFAULT_KNOWLEDGE_COLLECTION_CONFIG } from "@/lib/limits";

type FieldName =
  | "name"
  | "retrieverType"
  | "retrievalMode"
  | "chunkSize"
  | "chunkOverlap"
  | "topK"
  | "scoreThreshold"
  | "vectorWeight"
  | "keywordWeight";

type FieldErrors = Partial<Record<FieldName, string>>;

export type KnowledgeCollectionFormProps = {
  collection?: KnowledgeCollection | null;
  onCancel?: () => void;
  onSaved?: (collection: KnowledgeCollection) => void;
  onSavingChange?: (saving: boolean) => void;
  scope: ResourceScope;
};

function configValue<K extends keyof KnowledgeCollectionConfig>(
  collection: KnowledgeCollection | null,
  key: K,
): KnowledgeCollectionConfig[K] {
  return collection?.config[key] ?? DEFAULT_KNOWLEDGE_COLLECTION_CONFIG[key];
}

export function KnowledgeCollectionForm({
  collection = null,
  onCancel,
  onSaved,
  onSavingChange,
  scope,
}: KnowledgeCollectionFormProps) {
  const { t } = useTranslation("knowledge");
  const idPrefix = useId();
  const [name, setName] = useState(collection?.name ?? "");
  const [retrieverType, setRetrieverType] = useState<KnowledgeRetrieverType>(
    configValue(collection, "retriever_type"),
  );
  const [retrievalMode, setRetrievalMode] = useState<KnowledgeRetrievalMode>(
    configValue(collection, "retrieval_mode"),
  );
  const [chunkSize, setChunkSize] = useState(String(configValue(collection, "chunk_size")));
  const [chunkOverlap, setChunkOverlap] = useState(
    String(configValue(collection, "chunk_overlap")),
  );
  const [topK, setTopK] = useState(String(configValue(collection, "top_k")));
  const [scoreThreshold, setScoreThreshold] = useState(
    String(configValue(collection, "score_threshold")),
  );
  const [vectorWeight, setVectorWeight] = useState(
    String(configValue(collection, "vector_weight")),
  );
  const [keywordWeight, setKeywordWeight] = useState(
    String(configValue(collection, "keyword_weight")),
  );
  const [modeResetNotice, setModeResetNotice] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [saveErrorKey, setSaveErrorKey] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
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
    setName(collection?.name ?? "");
    setRetrieverType(configValue(collection, "retriever_type"));
    setRetrievalMode(configValue(collection, "retrieval_mode"));
    setChunkSize(String(configValue(collection, "chunk_size")));
    setChunkOverlap(String(configValue(collection, "chunk_overlap")));
    setTopK(String(configValue(collection, "top_k")));
    setScoreThreshold(String(configValue(collection, "score_threshold")));
    setVectorWeight(String(configValue(collection, "vector_weight")));
    setKeywordWeight(String(configValue(collection, "keyword_weight")));
    setModeResetNotice(false);
    setFieldErrors({});
    setSaveErrorKey(null);
    setSaving(false);
    onSavingChange?.(false);
  }, [collection, onSavingChange, scope]);

  function validate(): {
    name: string;
    config: KnowledgeCollectionConfig;
  } | null {
    const errors: FieldErrors = {};
    const trimmedName = name.trim();
    if (!trimmedName) {
      errors.name = "validation.nameRequired";
    }

    const parsedChunkSize = Number(chunkSize);
    if (
      !chunkSize.trim() ||
      !Number.isInteger(parsedChunkSize) ||
      parsedChunkSize < KNOWLEDGE_COLLECTION_LIMITS.chunkSizeMin ||
      parsedChunkSize > KNOWLEDGE_COLLECTION_LIMITS.chunkSizeMax
    ) {
      errors.chunkSize = "validation.chunkSize";
    }

    const parsedChunkOverlap = Number(chunkOverlap);
    if (
      !chunkOverlap.trim() ||
      !Number.isInteger(parsedChunkOverlap) ||
      parsedChunkOverlap < KNOWLEDGE_COLLECTION_LIMITS.chunkOverlapMin ||
      parsedChunkOverlap > KNOWLEDGE_COLLECTION_LIMITS.chunkOverlapMax
    ) {
      errors.chunkOverlap = "validation.chunkOverlap";
    } else if (
      !errors.chunkSize &&
      parsedChunkOverlap * 2 > parsedChunkSize
    ) {
      errors.chunkOverlap = "validation.chunkOverlapHalf";
    }

    const parsedTopK = Number(topK);
    if (
      !topK.trim() ||
      !Number.isInteger(parsedTopK) ||
      parsedTopK < KNOWLEDGE_COLLECTION_LIMITS.topKMin ||
      parsedTopK > KNOWLEDGE_COLLECTION_LIMITS.topKMax
    ) {
      errors.topK = "validation.topK";
    }

    const parsedScoreThreshold = Number(scoreThreshold);
    if (
      !scoreThreshold.trim() ||
      !Number.isFinite(parsedScoreThreshold) ||
        parsedScoreThreshold < KNOWLEDGE_COLLECTION_LIMITS.scoreThresholdMin ||
        parsedScoreThreshold > KNOWLEDGE_COLLECTION_LIMITS.scoreThresholdMax
    ) {
      errors.scoreThreshold = "validation.scoreThreshold";
    }

    const parsedVectorWeight = Number(vectorWeight);
    if (
      !vectorWeight.trim() ||
      !Number.isFinite(parsedVectorWeight) ||
      parsedVectorWeight < KNOWLEDGE_COLLECTION_LIMITS.weightMin ||
      parsedVectorWeight > KNOWLEDGE_COLLECTION_LIMITS.weightMax
    ) {
      errors.vectorWeight = "validation.weight";
    }
    const parsedKeywordWeight = Number(keywordWeight);
    if (
      !keywordWeight.trim() ||
      !Number.isFinite(parsedKeywordWeight) ||
      parsedKeywordWeight < KNOWLEDGE_COLLECTION_LIMITS.weightMin ||
      parsedKeywordWeight > KNOWLEDGE_COLLECTION_LIMITS.weightMax
    ) {
      errors.keywordWeight = "validation.weight";
    }
    if (
      !errors.vectorWeight &&
      !errors.keywordWeight &&
      parsedVectorWeight + parsedKeywordWeight <= 0
    ) {
      errors.keywordWeight = "validation.weightSum";
    }

    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      return null;
    }
    return {
      name: trimmedName,
      config: {
        retriever_type: retrieverType,
        retrieval_mode: retrievalMode,
        chunk_size: parsedChunkSize,
        chunk_overlap: parsedChunkOverlap,
        top_k: parsedTopK,
        score_threshold: parsedScoreThreshold,
        vector_weight: parsedVectorWeight,
        keyword_weight: parsedKeywordWeight,
      },
    };
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (savingRef.current) {
      return;
    }
    const payload = validate();
    if (payload === null) {
      return;
    }

    const operation = ++operationRef.current;
    savingRef.current = true;
    setSaving(true);
    setSaveErrorKey(null);
    onSavingChange?.(true);
    try {
      const saved =
        collection === null
          ? await createKnowledgeCollection(scope, payload)
          : await updateKnowledgeCollection(scope, collection.id, {
              ...payload,
              is_active: collection.is_active,
            });
      if (!mountedRef.current || operationRef.current !== operation) {
        return;
      }
      if (collection === null) {
        setName("");
        setRetrieverType(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.retriever_type);
        setRetrievalMode(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.retrieval_mode);
        setChunkSize(String(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.chunk_size));
        setChunkOverlap(String(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.chunk_overlap));
        setTopK(String(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.top_k));
        setScoreThreshold(String(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.score_threshold));
        setVectorWeight(String(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.vector_weight));
        setKeywordWeight(String(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.keyword_weight));
        setModeResetNotice(false);
      }
      onSaved?.(saved);
    } catch (error) {
      if (!mountedRef.current || operationRef.current !== operation) {
        return;
      }
      setSaveErrorKey(
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

  function errorId(field: FieldName) {
    return `${idPrefix}-${field}-error`;
  }

  function fieldError(field: FieldName) {
    const key = fieldErrors[field];
    return key ? (
      <small className="form-field-error" id={errorId(field)} role="alert">
        {t(key)}
      </small>
    ) : null;
  }

  const editing = collection !== null;

  function selectRetriever(next: KnowledgeRetrieverType) {
    setRetrieverType(next);
    if (next === "qdrant" && retrievalMode !== "vector") {
      setRetrievalMode("vector");
      setModeResetNotice(true);
    } else {
      setModeResetNotice(false);
    }
  }

  return (
    <form
      className="knowledge-collection-form"
      data-scope={scope}
      noValidate
      onSubmit={(event) => void handleSubmit(event)}
    >
      <div className="knowledge-form-field">
        <label>
          {t("form.name")}
          <input
            aria-describedby={fieldErrors.name ? errorId("name") : undefined}
            aria-invalid={fieldErrors.name ? true : undefined}
            autoFocus
            maxLength={MAX_KNOWLEDGE_COLLECTION_NAME_LENGTH}
            onChange={(event) => setName(event.target.value)}
            required
            value={name}
          />
        </label>
        {fieldError("name")}
      </div>

      <fieldset className="knowledge-config-fieldset">
        <legend>{t("form.retrievalConfig")}</legend>
        <div className="knowledge-config-grid">
          <div className="knowledge-form-field">
            <label>
              {t("form.retrieverType")}
              <select
                aria-describedby={editing ? `${idPrefix}-retriever-immutable` : undefined}
                data-testid="knowledge-retriever-select"
                disabled={editing}
                onChange={(event) =>
                  selectRetriever(event.target.value as KnowledgeRetrieverType)
                }
                value={retrieverType}
              >
                <option value="elasticsearch">{t("retrievers.elasticsearch")}</option>
                <option value="qdrant">{t("retrievers.qdrant")}</option>
              </select>
            </label>
            {editing ? (
              <small
                className="form-hint"
                data-testid="knowledge-retriever-immutable-hint"
                id={`${idPrefix}-retriever-immutable`}
              >
                {t("form.retrieverImmutable")}
              </small>
            ) : null}
          </div>
          <div className="knowledge-form-field">
            <label>
              {t("form.retrievalMode")}
              <select
                onChange={(event) =>
                  setRetrievalMode(event.target.value as KnowledgeRetrievalMode)
                }
                value={retrievalMode}
              >
                <option value="vector">{t("retrievalModes.vector")}</option>
                {retrieverType === "elasticsearch" ? (
                  <>
                    <option value="keyword">{t("retrievalModes.keyword")}</option>
                    <option value="hybrid">{t("retrievalModes.hybrid")}</option>
                  </>
                ) : null}
              </select>
            </label>
          </div>
          <div className="knowledge-form-field">
            <label>
              {t("form.chunkSize")}
              <input
                aria-describedby={fieldErrors.chunkSize ? errorId("chunkSize") : undefined}
                aria-invalid={fieldErrors.chunkSize ? true : undefined}
                inputMode="numeric"
                max={KNOWLEDGE_COLLECTION_LIMITS.chunkSizeMax}
                min={KNOWLEDGE_COLLECTION_LIMITS.chunkSizeMin}
                onChange={(event) => setChunkSize(event.target.value)}
                step={1}
                type="number"
                value={chunkSize}
              />
            </label>
            {fieldError("chunkSize")}
          </div>
          <div className="knowledge-form-field">
            <label>
              {t("form.chunkOverlap")}
              <input
                aria-describedby={
                  fieldErrors.chunkOverlap ? errorId("chunkOverlap") : undefined
                }
                aria-invalid={fieldErrors.chunkOverlap ? true : undefined}
                inputMode="numeric"
                max={KNOWLEDGE_COLLECTION_LIMITS.chunkOverlapMax}
                min={KNOWLEDGE_COLLECTION_LIMITS.chunkOverlapMin}
                onChange={(event) => setChunkOverlap(event.target.value)}
                step={1}
                type="number"
                value={chunkOverlap}
              />
            </label>
            {fieldError("chunkOverlap")}
          </div>
          <div className="knowledge-form-field">
            <label>
              {t("form.topK")}
              <input
                aria-describedby={fieldErrors.topK ? errorId("topK") : undefined}
                aria-invalid={fieldErrors.topK ? true : undefined}
                inputMode="numeric"
                max={KNOWLEDGE_COLLECTION_LIMITS.topKMax}
                min={KNOWLEDGE_COLLECTION_LIMITS.topKMin}
                onChange={(event) => setTopK(event.target.value)}
                step={1}
                type="number"
                value={topK}
              />
            </label>
            {fieldError("topK")}
          </div>
          <div className="knowledge-form-field">
            <label>
              {t("form.scoreThreshold")}
              <input
                aria-describedby={
                  fieldErrors.scoreThreshold ? errorId("scoreThreshold") : undefined
                }
                aria-invalid={fieldErrors.scoreThreshold ? true : undefined}
                max={KNOWLEDGE_COLLECTION_LIMITS.scoreThresholdMax}
                min={KNOWLEDGE_COLLECTION_LIMITS.scoreThresholdMin}
                onChange={(event) => setScoreThreshold(event.target.value)}
                step="any"
                type="number"
                value={scoreThreshold}
              />
            </label>
            {fieldError("scoreThreshold")}
          </div>
          {retrievalMode === "hybrid" ? (
            <>
              <div className="knowledge-form-field">
                <label>
                  {t("form.vectorWeight")}
                  <input
                    aria-describedby={fieldErrors.vectorWeight ? errorId("vectorWeight") : undefined}
                    aria-invalid={fieldErrors.vectorWeight ? true : undefined}
                    max={KNOWLEDGE_COLLECTION_LIMITS.weightMax}
                    min={KNOWLEDGE_COLLECTION_LIMITS.weightMin}
                    onChange={(event) => setVectorWeight(event.target.value)}
                    step="any"
                    type="number"
                    value={vectorWeight}
                  />
                </label>
                {fieldError("vectorWeight")}
              </div>
              <div className="knowledge-form-field">
                <label>
                  {t("form.keywordWeight")}
                  <input
                    aria-describedby={fieldErrors.keywordWeight ? errorId("keywordWeight") : undefined}
                    aria-invalid={fieldErrors.keywordWeight ? true : undefined}
                    max={KNOWLEDGE_COLLECTION_LIMITS.weightMax}
                    min={KNOWLEDGE_COLLECTION_LIMITS.weightMin}
                    onChange={(event) => setKeywordWeight(event.target.value)}
                    step="any"
                    type="number"
                    value={keywordWeight}
                  />
                </label>
                {fieldError("keywordWeight")}
              </div>
            </>
          ) : null}
        </div>
        {modeResetNotice ? (
          <p className="form-notice" role="status">{t("form.qdrantModeReset")}</p>
        ) : null}
        <p className="form-hint">{t("form.indexingHint")}</p>
        <p className="form-hint">{t("form.retrievalHint")}</p>
      </fieldset>

      {saveErrorKey ? (
        <p className="form-error" role="alert">
          {t(saveErrorKey)}
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
        <button className="knowledge-primary-action" disabled={saving} type="submit">
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
