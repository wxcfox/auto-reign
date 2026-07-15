"use client";

import { Upload } from "lucide-react";
import { useRef, useState, type ChangeEvent } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { uploadKnowledgeDocument } from "@/lib/api";
import type { KnowledgeDocument } from "@/lib/types";

export type KnowledgeUploaderProps = {
  collectionId: string;
  disabled?: boolean;
  onUploaded?: (document: KnowledgeDocument) => void;
};

export function KnowledgeUploader({
  collectionId,
  disabled = false,
  onUploaded,
}: KnowledgeUploaderProps) {
  const { t } = useTranslation("knowledge");
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploadingName, setUploadingName] = useState<string | null>(null);
  const [uploadedName, setUploadedName] = useState<string | null>(null);
  const [error, setError] = useState(false);

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = "";
    if (!file || disabled || uploadingName !== null) {
      return;
    }
    setUploadingName(file.name);
    setUploadedName(null);
    setError(false);
    try {
      const uploaded = await uploadKnowledgeDocument(collectionId, file);
      setUploadedName(uploaded.name);
      onUploaded?.(uploaded);
    } catch {
      setError(true);
    } finally {
      setUploadingName(null);
    }
  }

  return (
    <div className="knowledge-uploader">
      <div>
        <h3>{t("uploader.title")}</h3>
        <p>{t("uploader.description")}</p>
      </div>
      <input
        accept=".txt,.md,.pdf,.docx"
        aria-label={t("uploader.select")}
        className="sr-only"
        disabled={disabled || uploadingName !== null}
        onChange={(event) => void handleFile(event)}
        ref={inputRef}
        type="file"
      />
      <button
        className="knowledge-primary-action"
        disabled={disabled || uploadingName !== null}
        onClick={() => inputRef.current?.click()}
        type="button"
      >
        <Upload aria-hidden="true" size={16} />
        {t("uploader.select")}
      </button>
      {uploadingName ? (
        <p className="knowledge-uploader__status" role="status">
          {t("uploader.uploading", { name: uploadingName })}
        </p>
      ) : uploadedName ? (
        <p className="knowledge-uploader__success" role="status">
          {t("uploader.success", { name: uploadedName })}
        </p>
      ) : null}
      {error ? (
        <p className="form-error" role="alert">
          {t("uploader.error")}
        </p>
      ) : null}
    </div>
  );
}
