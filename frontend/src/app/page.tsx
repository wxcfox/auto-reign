"use client";

import { FileText, Folder, Pencil, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { StatusPill } from "@/components/StatusPill";
import { deleteWorkspaceArtifact, getWorkspaceFileContent, getWorkspaceFiles } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type {
  WorkspaceDirectoryEntry,
  WorkspaceFileContentResponse,
  WorkspaceFileEntry,
} from "@/lib/types";
import { useTranslation } from "@/hooks/useTranslation";

type WorkspaceSidebarEntry = {
  key: string;
  type: "directory" | "file";
  label: string;
  depth: number;
  count: number | null;
  path: string;
  files: WorkspaceFileEntry[];
};

type WorkspaceTableRow =
  | {
      key: string;
      type: "directory";
      directory: WorkspaceDirectoryEntry;
    }
  | {
      key: string;
      type: "file";
      file: WorkspaceFileEntry;
    };

function directoryDisplayPath(entry: WorkspaceSidebarEntry) {
  return entry.type === "directory" ? `${entry.path}/` : entry.path;
}

function directoryToEntry(directory: WorkspaceDirectoryEntry): WorkspaceSidebarEntry {
  return {
    key: `dir:${directory.relative_path}`,
    type: "directory",
    label: directory.name,
    depth: 0,
    count: directory.file_count,
    path: directory.relative_path,
    files: directory.files,
  };
}

function fileToEntry(file: WorkspaceFileEntry): WorkspaceSidebarEntry {
  return {
    key: `file:${file.relative_path}`,
    type: "file",
    label: file.name,
    depth: 0,
    count: null,
    path: file.relative_path,
    files: [file],
  };
}

function buildSidebarEntries(directories: WorkspaceDirectoryEntry[]): WorkspaceSidebarEntry[] {
  const root = directories.find((directory) => directory.relative_path === "");
  const directoryEntries = directories
    .filter((directory) => directory.relative_path && directory.depth === 1)
    .map(directoryToEntry);
  const rootFileEntries = (root?.files ?? []).map(fileToEntry);
  return [...directoryEntries, ...rootFileEntries];
}

function entryByKey(
  key: string,
  directories: WorkspaceDirectoryEntry[],
): WorkspaceSidebarEntry | null {
  if (!key) {
    return null;
  }
  if (key.startsWith("dir:")) {
    const relativePath = key.slice("dir:".length);
    const directory = directories.find((item) => item.relative_path === relativePath);
    return directory ? directoryToEntry(directory) : null;
  }
  if (key.startsWith("file:")) {
    const relativePath = key.slice("file:".length);
    for (const directory of directories) {
      const file = directory.files.find((item) => item.relative_path === relativePath);
      if (file) {
        return fileToEntry(file);
      }
    }
  }
  return null;
}

function defaultSidebarEntry(entries: WorkspaceSidebarEntry[]) {
  return (
    entries.find((entry) => entry.type === "directory" && (entry.count ?? 0) > 0) ??
    entries[0] ??
    null
  );
}

function parentDirectoryPath(relativePath: string) {
  const parts = relativePath.split("/");
  parts.pop();
  return parts.join("/");
}

function childDirectoryRows(
  entry: WorkspaceSidebarEntry | null,
  directories: WorkspaceDirectoryEntry[],
): WorkspaceTableRow[] {
  if (!entry || entry.type !== "directory") {
    return [];
  }
  return directories
    .filter(
      (directory) =>
        directory.relative_path && parentDirectoryPath(directory.relative_path) === entry.path,
    )
    .map((directory) => ({
      key: `dir:${directory.relative_path}`,
      type: "directory" as const,
      directory,
    }));
}

function isMarkdownFile(name: string) {
  return /\.(md|markdown)$/i.test(name);
}

export default function DashboardPage() {
  const { getCurrentLanguage, t } = useTranslation("dashboard");
  const [directories, setDirectories] = useState<WorkspaceDirectoryEntry[]>([]);
  const [selectedEntryKey, setSelectedEntryKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [preview, setPreview] = useState<WorkspaceFileContentResponse | null>(null);
  const [previewLoadingPath, setPreviewLoadingPath] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  async function loadFiles() {
    setLoading(true);
    try {
      const response = await getWorkspaceFiles();
      setDirectories(response.directories);
      const entries = buildSidebarEntries(response.directories);
      setSelectedEntryKey((current) =>
        entryByKey(current, response.directories)?.key ??
        defaultSidebarEntry(entries)?.key ??
        "",
      );
    } catch (loadError) {
      setError(getErrorMessage(loadError, t, "common:errors.generic_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadFiles();
  }, []);

  const totalFileCount = useMemo(
    () => directories.reduce((total, directory) => total + directory.file_count, 0),
    [directories],
  );
  const sidebarEntries = useMemo(() => buildSidebarEntries(directories), [directories]);
  const selectedEntry = useMemo(
    () =>
      entryByKey(selectedEntryKey, directories) ??
      defaultSidebarEntry(sidebarEntries),
    [selectedEntryKey, sidebarEntries, directories],
  );
  const tableRows = useMemo(
    () => [
      ...childDirectoryRows(selectedEntry, directories),
      ...(selectedEntry?.files ?? []).map((file) => ({
        key: `file:${file.relative_path}`,
        type: "file" as const,
        file,
      })),
    ],
    [selectedEntry, directories],
  );

  function formatDate(value: string) {
    try {
      return new Intl.DateTimeFormat(getCurrentLanguage() === "zh-CN" ? "zh-CN" : "en", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value));
    } catch {
      return value;
    }
  }

  function fileStatus(file: WorkspaceFileEntry) {
    if (file.recovery_required) {
      return t("common:states.checking");
    }
    return t(`library:states.${file.index_status}`, file.index_status);
  }

  function isSidebarEntryActive(entry: WorkspaceSidebarEntry) {
    if (!selectedEntry) {
      return false;
    }
    if (entry.key === selectedEntry.key) {
      return true;
    }
    return (
      entry.type === "directory" &&
      selectedEntry.type === "directory" &&
      selectedEntry.path.startsWith(`${entry.path}/`)
    );
  }

  async function openFilePreview(file: WorkspaceFileEntry) {
    setError(null);
    setMessage(null);
    setPreview(null);
    setPreviewError(null);
    setPreviewLoadingPath(file.relative_path);
    try {
      setPreview(await getWorkspaceFileContent(file.relative_path));
    } catch (previewLoadError) {
      setPreviewError(getErrorMessage(previewLoadError, t, "common:errors.generic_load"));
    } finally {
      setPreviewLoadingPath(null);
    }
  }

  function handleSelectEntry(entry: WorkspaceSidebarEntry) {
    setSelectedEntryKey(entry.key);
    setPreview(null);
    setPreviewError(null);
    if (entry.type === "file" && entry.files[0] && !entry.files[0].artifact_id) {
      void openFilePreview(entry.files[0]);
    }
  }

  function handleOpenDirectory(directory: WorkspaceDirectoryEntry) {
    setSelectedEntryKey(`dir:${directory.relative_path}`);
    setPreview(null);
    setPreviewError(null);
  }

  async function handleDelete(file: WorkspaceFileEntry) {
    if (!file.artifact_id) {
      return;
    }
    const confirmed = window.confirm(t("library:delete_confirm", { name: file.name }));
    if (!confirmed) {
      return;
    }
    setError(null);
    setMessage(null);
    setDeletingId(file.artifact_id);
    try {
      await deleteWorkspaceArtifact(file.artifact_id);
      await loadFiles();
      setMessage(t("library:delete_success"));
    } catch (deleteError) {
      setError(getErrorMessage(deleteError, t, "common:errors.generic_save"));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("files.eyebrow")}</p>
          <h1>{t("workbench_title")}</h1>
        </div>
        <p className="page-summary">{t("files.total_count", { count: totalFileCount })}</p>
      </header>

      <section
        className="library-browser workspace-browser"
        aria-labelledby="workspace-file-heading"
      >
        <aside className="library-sidebar" aria-label={t("files.folders_title")}>
          <div className="library-sidebar-header">
            <p className="eyebrow">{t("files.folders_title")}</p>
          </div>
          {loading ? <p className="empty-state">{t("files.loading")}</p> : null}
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
          {!loading && !error && sidebarEntries.length === 0 ? (
            <p className="empty-state">{t("files.empty")}</p>
          ) : null}
          {!loading && !error && sidebarEntries.length > 0 ? (
            <div className="library-category-list workspace-folder-list">
              {sidebarEntries.map((entry) => {
                const active = isSidebarEntryActive(entry);
                return (
                  <button
                    aria-label={
                      entry.count === null ? entry.label : `${entry.label} ${entry.count}`
                    }
                    aria-pressed={active}
                    data-active={active}
                    key={entry.key}
                    onClick={() => handleSelectEntry(entry)}
                    style={{ paddingLeft: `${10 + entry.depth * 14}px` }}
                    type="button"
                  >
                    <span>{entry.label}</span>
                    <strong>{entry.count ?? ""}</strong>
                  </button>
                );
              })}
            </div>
          ) : null}
        </aside>

        <div className="library-files">
          <div className="section-heading library-file-heading">
            <div>
              <p className="eyebrow">
                {selectedEntry ? directoryDisplayPath(selectedEntry) : t("files.title")}
              </p>
              <h2 id="workspace-file-heading">{t("files.title")}</h2>
            </div>
            {selectedEntry && selectedEntry.count !== null ? (
              <p className="page-summary">
                {selectedEntry.count}
              </p>
            ) : null}
          </div>

          {message ? (
            <p className="form-success" role="status">
              {message}
            </p>
          ) : null}
          {error && !loading ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}

          {selectedEntry && tableRows.length === 0 ? (
            <p className="empty-state">{t("files.empty_folder")}</p>
          ) : null}

          {selectedEntry && tableRows.length > 0 ? (
            <div className="library-table-shell">
              <div className="library-table-scroll">
                <div
                  className="library-table"
                  role="table"
                  aria-label={t("files.title")}
                >
                  <div className="library-table-row library-table-head" role="row">
                    <span role="columnheader">{t("library:table.name")}</span>
                    <span role="columnheader">{t("library:table.owner")}</span>
                    <span role="columnheader">{t("library:table.created_at")}</span>
                    <span role="columnheader">{t("library:table.updated_at")}</span>
                    <span role="columnheader">{t("library:table.actions")}</span>
                  </div>
                  {tableRows.map((row) =>
                    row.type === "directory" ? (
                      <div className="library-table-row" key={row.key} role="row">
                        <div className="library-file-name" role="cell">
                          <span className="library-file-icon" aria-hidden="true">
                            <Folder size={16} />
                          </span>
                          <div>
                            <button
                              className="workspace-file-link-button"
                              onClick={() => handleOpenDirectory(row.directory)}
                              type="button"
                            >
                              {row.directory.name}
                            </button>
                            <span>{row.directory.relative_path}/</span>
                          </div>
                          <StatusPill label={t("files.folder")} tone="neutral" />
                        </div>
                        <span className="library-muted-cell" role="cell">
                          {t("library:owners.workspace")}
                        </span>
                        <time
                          className="library-muted-cell"
                          dateTime={row.directory.created_at}
                          role="cell"
                        >
                          {formatDate(row.directory.created_at)}
                        </time>
                        <time
                          className="library-muted-cell"
                          dateTime={row.directory.updated_at}
                          role="cell"
                        >
                          {formatDate(row.directory.updated_at)}
                        </time>
                        <div className="library-actions" role="cell" />
                      </div>
                    ) : (
                      <div className="library-table-row" key={row.key} role="row">
                        <div className="library-file-name" role="cell">
                          <span className="library-file-icon" aria-hidden="true">
                            <FileText size={16} />
                          </span>
                          <div>
                            {row.file.artifact_id ? (
                              <Link href={`/library/${row.file.artifact_id}`}>{row.file.name}</Link>
                            ) : (
                              <button
                                className="workspace-file-link-button"
                                onClick={() => void openFilePreview(row.file)}
                                type="button"
                              >
                                {row.file.name}
                              </button>
                            )}
                            <span>{row.file.relative_path}</span>
                          </div>
                          <StatusPill
                            label={fileStatus(row.file)}
                            tone={row.file.recovery_required ? "warning" : "success"}
                          />
                        </div>
                        <span className="library-muted-cell" role="cell">
                          {row.file.kind === "file"
                            ? t("library:owners.workspace")
                            : t(`library:owners.${row.file.owner}`, row.file.owner)}
                        </span>
                        <time
                          className="library-muted-cell"
                          dateTime={row.file.created_at}
                          role="cell"
                        >
                          {formatDate(row.file.created_at)}
                        </time>
                        <time
                          className="library-muted-cell"
                          dateTime={row.file.updated_at}
                          role="cell"
                        >
                          {formatDate(row.file.updated_at)}
                        </time>
                        <div className="library-actions" role="cell">
                          {row.file.artifact_id ? (
                            <>
                              <Link
                                aria-label={t("library:edit_named", { name: row.file.name })}
                                className="library-action-button"
                                href={`/library/${row.file.artifact_id}`}
                                title={t("library:actions.edit")}
                              >
                                <Pencil size={15} aria-hidden="true" />
                                <span className="sr-only">{t("library:actions.edit")}</span>
                              </Link>
                              <button
                                aria-label={t("library:delete_named", { name: row.file.name })}
                                className="library-action-button library-action-danger"
                                disabled={deletingId === row.file.artifact_id}
                                onClick={() => void handleDelete(row.file)}
                                title={t("library:actions.delete")}
                                type="button"
                              >
                                <Trash2 size={15} aria-hidden="true" />
                                <span className="sr-only">{t("library:actions.delete")}</span>
                              </button>
                            </>
                          ) : null}
                        </div>
                      </div>
                    ),
                  )}
                </div>
              </div>
            </div>
          ) : null}
          {previewLoadingPath ? (
            <p className="empty-state">{t("files.preview_loading")}</p>
          ) : null}
          {previewError ? (
            <p className="form-error" role="alert">
              {previewError}
            </p>
          ) : null}
          {preview ? (
            <section className="content-surface workspace-file-preview" aria-label={t("files.preview_title")}>
              <div className="section-heading">
                <div>
                  <p className="eyebrow">{preview.relative_path}</p>
                  <h3>{preview.name}</h3>
                </div>
                <time className="page-summary" dateTime={preview.updated_at}>
                  {formatDate(preview.updated_at)}
                </time>
              </div>
              {isMarkdownFile(preview.name) ? (
                <MarkdownView content={preview.content} />
              ) : (
                <pre className="workspace-file-preview-pre">{preview.content}</pre>
              )}
            </section>
          ) : null}
        </div>
      </section>
    </div>
  );
}
