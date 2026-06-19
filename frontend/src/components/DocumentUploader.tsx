"use client";

import { useState, type ChangeEvent, type FormEvent } from "react";
import { Upload } from "lucide-react";

import { uploadDocument } from "@/lib/api";
import type { DocumentRecord } from "@/lib/types";

type DocumentUploaderProps = {
  onUploaded: (document: DocumentRecord) => void;
};

export function DocumentUploader({ onUploaded }: DocumentUploaderProps) {
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
      setError(uploadError instanceof Error ? uploadError.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <form className="upload-tool" onSubmit={handleSubmit}>
      <div>
        <label className="field-label" htmlFor="document-file">
          Document
        </label>
        <input
          accept=".md,.txt,text/markdown,text/plain"
          id="document-file"
          onChange={handleFileChange}
          type="file"
        />
        <p className="field-hint">Markdown/TXT, processed and indexed locally.</p>
      </div>
      <button className="button button-primary" disabled={!file || uploading} type="submit">
        <Upload aria-hidden="true" size={17} />
        {uploading ? "Uploading..." : "Upload"}
      </button>
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}
