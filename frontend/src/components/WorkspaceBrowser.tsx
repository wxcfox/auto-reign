"use client";

import { FileText, Folder, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { WorkspaceEditor } from "@/components/WorkspaceEditor";
import { useTranslation } from "@/hooks/useTranslation";
import { deleteWorkspaceFile, listWorkspaceFiles } from "@/lib/api";
import type { WorkspaceFileItem } from "@/lib/types";

export type WorkspaceBrowserProps = {
  workspaceId: string;
};

export function WorkspaceBrowser({ workspaceId }: WorkspaceBrowserProps) {
  return <WorkspaceBrowserInstance key={workspaceId} workspaceId={workspaceId} />;
}

function WorkspaceBrowserInstance({ workspaceId }: WorkspaceBrowserProps) {
  const { t } = useTranslation("workspaces");
  const [directory, setDirectory] = useState("");
  const [items, setItems] = useState<WorkspaceFileItem[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [deleteError, setDeleteError] = useState(false);
  const [deletingPath, setDeletingPath] = useState<string | null>(null);
  const [reloadVersion, setReloadVersion] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(false);
    listWorkspaceFiles(workspaceId, directory)
      .then((response) => {
        if (active) {
          setItems(response.items);
        }
      })
      .catch(() => {
        if (active) {
          setItems([]);
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
  }, [directory, reloadVersion, workspaceId]);

  function openDirectory(path: string) {
    setDirectory(path);
    setSelectedPath(null);
    setDeleteError(false);
  }

  async function handleDelete(item: WorkspaceFileItem) {
    if (item.is_directory || item.path === "AGENTS.md" || deletingPath !== null) {
      return;
    }
    const confirmed = window.confirm(
      t("browser.deleteConfirm", {
        defaultValue: "Delete {{name}}? This cannot be undone.",
        name: item.name,
      }),
    );
    if (!confirmed) {
      return;
    }
    setDeletingPath(item.path);
    setDeleteError(false);
    try {
      await deleteWorkspaceFile(workspaceId, item.path);
      setSelectedPath((current) => (current === item.path ? null : current));
      setReloadVersion((current) => current + 1);
    } catch {
      setDeleteError(true);
    } finally {
      setDeletingPath(null);
    }
  }

  const pathSegments = directory ? directory.split("/") : [];

  return (
    <section
      className="workspace-browser agent-home-browser"
      data-workspace-id={workspaceId}
      data-testid="workspace-browser"
    >
      <nav
        aria-label={t("browser.breadcrumb", { defaultValue: "Workspace folders" })}
        className="workspace-browser__breadcrumb"
      >
        <button
          aria-label={t("browser.root", { defaultValue: "Workspace root" })}
          disabled={directory === ""}
          onClick={() => openDirectory("")}
          type="button"
        >
          {t("browser.rootShort", { defaultValue: "Root" })}
        </button>
        {pathSegments.map((segment, index) => {
          const path = pathSegments.slice(0, index + 1).join("/");
          return (
            <span key={path}>
              <span aria-hidden="true">/</span>
              <button
                disabled={path === directory}
                onClick={() => openDirectory(path)}
                type="button"
              >
                {segment}
              </button>
            </span>
          );
        })}
      </nav>

      {loading ? (
        <p className="workspace-state" role="status">
          {t("browser.loading", { defaultValue: "Loading files…" })}
        </p>
      ) : loadError ? (
        <div className="workspace-state workspace-state--error">
          <p role="alert">
            {t("browser.loadError", { defaultValue: "Could not load files." })}
          </p>
          <button onClick={() => setReloadVersion((current) => current + 1)} type="button">
            {t("actions.retry", { defaultValue: "Retry" })}
          </button>
        </div>
      ) : items.length === 0 ? (
        <p className="workspace-state">
          {t("browser.empty", { defaultValue: "This folder is empty." })}
        </p>
      ) : (
        <div className="resource-table-wrap">
          <table className="resource-table">
            <thead>
              <tr>
                <th scope="col">{t("browser.name", { defaultValue: "Name" })}</th>
                <th scope="col">{t("browser.type", { defaultValue: "Type" })}</th>
                <th scope="col">{t("browser.actions", { defaultValue: "Actions" })}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.path}>
                  <td>
                    <button
                      className="resource-table-name"
                      onClick={() =>
                        item.is_directory ? openDirectory(item.path) : setSelectedPath(item.path)
                      }
                      type="button"
                    >
                      {item.is_directory ? (
                        <Folder aria-hidden="true" size={15} />
                      ) : (
                        <FileText aria-hidden="true" size={15} />
                      )}
                      {item.name}
                    </button>
                  </td>
                  <td>
                    <span className="resource-status-badge">
                      {item.is_directory
                        ? t("browser.typeFolder", { defaultValue: "Folder" })
                        : t("browser.typeFile", { defaultValue: "File" })}
                    </span>
                  </td>
                  <td>
                    <div className="resource-table-actions">
                      {!item.is_directory && item.path !== "AGENTS.md" ? (
                        <button
                          aria-label={t("browser.delete", {
                            defaultValue: "Delete {{name}}",
                            name: item.path,
                          })}
                          className="resource-table-action--danger"
                          disabled={deletingPath !== null}
                          onClick={() => void handleDelete(item)}
                          type="button"
                        >
                          <Trash2 aria-hidden="true" size={15} />
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {deleteError ? (
        <p className="form-error" role="alert">
          {t("browser.deleteError", { defaultValue: "Could not delete the file." })}
        </p>
      ) : null}

      {selectedPath ? (
        <section className="workspace-browser__detail" aria-label={selectedPath}>
          <button
            aria-label={t("browser.closeFile", { defaultValue: "Close file" })}
            className="workspace-browser__detail-close"
            onClick={() => setSelectedPath(null)}
            type="button"
          >
            <X aria-hidden="true" size={16} />
          </button>
          <WorkspaceEditor
            onFileUpdated={() => setReloadVersion((current) => current + 1)}
            path={selectedPath}
            workspaceId={workspaceId}
          />
        </section>
      ) : null}
    </section>
  );
}
