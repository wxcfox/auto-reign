"use client";

import { useState, type ChangeEvent, type FormEvent } from "react";
import { Upload } from "lucide-react";

import { useTranslation } from "@/hooks/useTranslation";
import { uploadDocument } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { DocumentRecord } from "@/lib/types";

type DocumentUploaderProps = {
  onUploaded: (document: DocumentRecord) => void;
};

export function DocumentUploader({ onUploaded }: DocumentUploaderProps) {
  const { t } = useTranslation("library");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
    setError(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || uploading) {
      return;
    }

    const form = event.currentTarget;
    setUploading(true);
    setError(null);
    try {
      const document = await uploadDocument(file);
      onUploaded(document);
      setFile(null);
      form.reset();
    } catch (uploadError) {
      setError(getErrorMessage(uploadError, t, "common:errors.generic_upload"));
    } finally {
      setUploading(false);
    }
  }

  return (
    <form className="upload-tool" onSubmit={handleSubmit}>
      <div>
        <label className="field-label" htmlFor="document-file">
          {t("uploader.label")}
        </label>
        <input
          accept=".md,.txt,text/markdown,text/plain"
          id="document-file"
          onChange={handleFileChange}
          type="file"
        />
        <p className="field-hint">{t("uploader.hint")}</p>
      </div>
      <button className="button button-primary" disabled={!file || uploading} type="submit">
        <Upload aria-hidden="true" size={17} />
        {uploading ? t("uploader.uploading") : t("common:actions.upload")}
      </button>
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}
