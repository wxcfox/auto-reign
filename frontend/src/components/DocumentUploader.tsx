"use client";

import { useRef, useState, type ChangeEvent } from "react";
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
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFiles = Array.from(event.target.files ?? []);
    if (selectedFiles.length === 0 || uploading) {
      return;
    }

    setUploading(true);
    setError(null);
    try {
      const response = await uploadMaterials(selectedFiles);
      onUploaded(response);
    } catch (uploadError) {
      setError(getErrorMessage(uploadError, t, "common:errors.generic_upload"));
    } finally {
      event.target.value = "";
      setUploading(false);
    }
  }

  return (
    <div className="library-upload-control">
      <input
        accept=".md,.txt,.pdf,.docx,text/markdown,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        aria-label={t("upload_label")}
        className="sr-only"
        multiple
        onChange={handleFileChange}
        ref={inputRef}
        type="file"
      />
      <button
        className="button button-primary library-upload-button"
        disabled={uploading}
        onClick={() => inputRef.current?.click()}
        type="button"
      >
        <Upload aria-hidden="true" size={17} />
        {uploading ? t("uploader.uploading") : t("common:actions.upload")}
      </button>
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
