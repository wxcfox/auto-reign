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
  ResourceScope,
} from "@/lib/types";

export { DEFAULT_KNOWLEDGE_COLLECTION_CONFIG } from "@/lib/limits";

type FieldName =
  | "name"
  | "chunkSize"
  | "chunkOverlap"
  | "topK"
  | "scoreThreshold";

type FieldErrors = Partial<Record<FieldName, string>>;

export type KnowledgeCollectionFormProps = {
  collection?: KnowledgeCollection | null;
  onCancel?: () => void;
  onSaved?: (collection: KnowledgeCollection) => void;
  onSavingChange?: (saving: boolean) => void;
  scope: ResourceScope;
};

function configValue(collection: KnowledgeCollection | null, key: keyof KnowledgeCollectionConfig) {
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
  const [chunkSize, setChunkSize] = useState(String(configValue(collection, "chunk_size")));
  const [chunkOverlap, setChunkOverlap] = useState(
    String(configValue(collection, "chunk_overlap")),
  );
  const [topK, setTopK] = useState(String(configValue(collection, "top_k")));
  const [scoreThreshold, setScoreThreshold] = useState(
    collection?.config.score_threshold == null
      ? ""
      : String(collection.config.score_threshold),
  );
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
    setChunkSize(String(configValue(collection, "chunk_size")));
    setChunkOverlap(String(configValue(collection, "chunk_overlap")));
    setTopK(String(configValue(collection, "top_k")));
    setScoreThreshold(
      collection?.config.score_threshold == null
        ? ""
        : String(collection.config.score_threshold),
    );
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

    const thresholdText = scoreThreshold.trim();
    const parsedScoreThreshold = thresholdText === "" ? null : Number(thresholdText);
    if (
      parsedScoreThreshold !== null &&
      (!Number.isFinite(parsedScoreThreshold) ||
        parsedScoreThreshold < KNOWLEDGE_COLLECTION_LIMITS.scoreThresholdMin ||
        parsedScoreThreshold > KNOWLEDGE_COLLECTION_LIMITS.scoreThresholdMax)
    ) {
      errors.scoreThreshold = "validation.scoreThreshold";
    }

    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      return null;
    }
    return {
      name: trimmedName,
      config: {
        chunk_size: parsedChunkSize,
        chunk_overlap: parsedChunkOverlap,
        top_k: parsedTopK,
        score_threshold: parsedScoreThreshold,
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
        setChunkSize(String(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.chunk_size));
        setChunkOverlap(String(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.chunk_overlap));
        setTopK(String(DEFAULT_KNOWLEDGE_COLLECTION_CONFIG.top_k));
        setScoreThreshold("");
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
        </div>
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
