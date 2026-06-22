"use client";

import { useState, type ChangeEvent, type FormEvent } from "react";
import { Upload } from "lucide-react";

import { useTranslation } from "@/hooks/useTranslation";
import { uploadMaterials } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { UploadMaterialsResponse } from "@/lib/types";

type DocumentUploaderProps = {
  onUploaded: (response: UploadMaterialsResponse) => void;
};

export function DocumentUploader({ onUploaded }: DocumentUploaderProps) {
  const { t } = useTranslation("library");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setFiles(Array.from(event.target.files ?? []));
    setError(null);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (files.length === 0 || uploading) {
      return;
    }

    const form = event.currentTarget;
    setUploading(true);
    setError(null);
    try {
      const response = await uploadMaterials(files);
      onUploaded(response);
      setFiles([]);
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
          accept=".md,.txt,.pdf,.docx,text/markdown,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          id="document-file"
          multiple
          onChange={handleFileChange}
          type="file"
        />
        <p className="field-hint">{t("uploader.hint", "Markdown/TXT/PDF/DOCX files are organized automatically.")}</p>
      </div>
      <button className="button button-primary" disabled={files.length === 0 || uploading} type="submit">
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
