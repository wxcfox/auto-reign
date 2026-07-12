"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { readWorkspaceFile, writeWorkspaceFile } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { WorkspaceFileContent, WorkspaceScope } from "@/lib/types";

export type WorkspaceEditorProps = {
  onFileUpdated?: (file: WorkspaceFileContent) => void;
  path: string;
  scope: WorkspaceScope;
  workspaceId: string;
};

export function WorkspaceEditor({
  onFileUpdated,
  path,
  scope,
  workspaceId,
}: WorkspaceEditorProps) {
  const { t } = useTranslation("workspaces");
  const identity = `${scope}\u0000${workspaceId}\u0000${path}`;
  const loadedIdentity = useRef<string | null>(null);
  const [file, setFile] = useState<WorkspaceFileContent | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [reloadVersion, setReloadVersion] = useState(0);

  useEffect(() => {
    let active = true;
    const sameFile = loadedIdentity.current === identity;
    if (!sameFile) {
      setFile(null);
      setDraft("");
    }
    setLoading(true);
    setLoadError(false);
    setSaveError(false);
    readWorkspaceFile(scope, workspaceId, path)
      .then((opened) => {
        if (active) {
          loadedIdentity.current = identity;
          setFile(opened);
          setDraft(opened.content);
          setConflict(false);
        }
      })
      .catch(() => {
        if (active) {
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
  }, [identity, path, reloadVersion, scope, workspaceId]);

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (file === null || file.etag === null || saving || loading) {
      return;
    }
    setSaving(true);
    setSaveError(false);
    setConflict(false);
    try {
      const saved = await writeWorkspaceFile(scope, workspaceId, {
        path,
        content: draft,
        expected_etag: file.etag,
      });
      setFile(saved);
      setDraft(saved.content);
      onFileUpdated?.(saved);
    } catch (cause) {
      if (
        cause instanceof ApiError &&
        cause.status === 409 &&
        cause.code === "workspace_conflict"
      ) {
        setConflict(true);
      } else {
        setSaveError(true);
      }
    } finally {
      setSaving(false);
    }
  }

  function reload() {
    setConflict(false);
    setSaveError(false);
    setReloadVersion((current) => current + 1);
  }

  if (loading && file === null) {
    return (
      <p className="workspace-state" role="status">
        {t("editor.loading", { defaultValue: "Loading file…" })}
      </p>
    );
  }

  if (loadError && file === null) {
    return (
      <div className="workspace-state workspace-state--error">
        <p role="alert">
          {t("editor.loadError", { defaultValue: "Could not load the file." })}
        </p>
        <button onClick={reload} type="button">
          {t("actions.retry", { defaultValue: "Retry" })}
        </button>
      </div>
    );
  }

  if (file === null) {
    return null;
  }

  return (
    <form className="workspace-editor" onSubmit={(event) => void handleSave(event)}>
      <div className="workspace-editor__heading">
        <h2>{file.name}</h2>
        <span>{file.size_bytes ?? 0} B</span>
      </div>
      <label>
        {t("editor.fileContent", { defaultValue: "File content" })}
        <textarea
          disabled={loading || saving}
          onChange={(event) => setDraft(event.target.value)}
          rows={22}
          value={draft}
        />
      </label>
      {conflict ? (
        <div className="workspace-editor__conflict">
          <p role="alert">
            {t("editor.conflict", {
              defaultValue: "This file changed since you opened it. Your draft is preserved.",
            })}
          </p>
          <button disabled={loading || saving} onClick={reload} type="button">
            {t("actions.reload", { defaultValue: "Reload" })}
          </button>
        </div>
      ) : null}
      {loadError && file !== null ? (
        <p className="form-error" role="alert">
          {t("editor.reloadError", {
            defaultValue: "Could not reload the file. Your draft is preserved.",
          })}
        </p>
      ) : null}
      {saveError ? (
        <p className="form-error" role="alert">
          {t("editor.saveError", { defaultValue: "Could not save the file." })}
        </p>
      ) : null}
      <button
        className="workspace-primary-action"
        disabled={saving || loading || file.etag === null || draft === file.content}
        type="submit"
      >
        {saving
          ? t("actions.saving", { defaultValue: "Saving…" })
          : t("actions.save", { defaultValue: "Save" })}
      </button>
    </form>
  );
}
