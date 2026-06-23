"use client";

import Link from "next/link";
import { FileText, Pencil, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { DocumentUploader } from "@/components/DocumentUploader";
import { StatusPill } from "@/components/StatusPill";
import { useTranslation } from "@/hooks/useTranslation";
import {
  deleteWorkspaceArtifact,
  getWorkspaceArtifacts,
  rebuildWorkspaceIndex,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { WorkspaceArtifactSummary } from "@/lib/types";

const CATEGORY_ORDER = [
  "knowledge",
  "source",
  "candidate_profile",
  "target_profile",
  "practice",
  "report",
  "plan",
  "mastery",
  "extracted",
];

export default function LibraryPage() {
  const { getCurrentLanguage, t } = useTranslation("library");
  const [artifacts, setArtifacts] = useState<WorkspaceArtifactSummary[]>([]);
  const [keyword, setKeyword] = useState("");
  const [selectedKind, setSelectedKind] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  async function loadArtifacts() {
    setLoading(true);
    try {
      const response = await getWorkspaceArtifacts();
      setArtifacts(response.artifacts);
    } catch (loadError) {
      setError(getErrorMessage(loadError, t, "common:errors.generic_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadArtifacts();
  }, []);

  const filteredArtifacts = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return artifacts.filter((artifact) => {
      const matchesKeyword =
        !normalizedKeyword ||
        `${artifact.display_name} ${artifact.relative_path} ${artifact.kind}`
          .toLowerCase()
          .includes(normalizedKeyword);
      const matchesKind = !selectedKind || artifact.kind === selectedKind;
      return matchesKeyword && matchesKind;
    });
  }, [artifacts, keyword, selectedKind]);

  const categoryItems = useMemo(() => {
    const counts = artifacts.reduce<Record<string, number>>((current, artifact) => {
      current[artifact.kind] = (current[artifact.kind] ?? 0) + 1;
      return current;
    }, {});
    const orderedKinds = [
      ...CATEGORY_ORDER.filter((item) => counts[item]),
      ...Object.keys(counts)
        .filter((item) => !CATEGORY_ORDER.includes(item))
        .sort(),
    ];
    return [
      { kind: "", label: t("categories.all"), count: artifacts.length },
      ...orderedKinds.map((item) => ({
        kind: item,
        label: t(`kinds.${item}`, item),
        count: counts[item] ?? 0,
      })),
    ];
  }, [artifacts, t]);

  const previewArtifact = filteredArtifacts[0] ?? null;

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

  async function handleRebuildIndex() {
    setError(null);
    setMessage(null);
    try {
      await rebuildWorkspaceIndex();
      await getWorkspaceArtifacts().then((response) => setArtifacts(response.artifacts));
      setMessage(t("rebuild_success"));
    } catch (rebuildError) {
      setError(getErrorMessage(rebuildError, t, "common:errors.generic_save"));
    }
  }

  async function handleDelete(artifact: WorkspaceArtifactSummary) {
    const confirmed = window.confirm(t("delete_confirm", { name: artifact.display_name }));
    if (!confirmed) {
      return;
    }
    setError(null);
    setMessage(null);
    setDeletingId(artifact.id);
    try {
      await deleteWorkspaceArtifact(artifact.id);
      await loadArtifacts();
      setMessage(t("delete_success"));
    } catch (deleteError) {
      setError(getErrorMessage(deleteError, t, "common:errors.generic_save"));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="library-workspace">
      <header className="page-header library-header">
        <div>
          <p className="eyebrow">{t("eyebrow")}</p>
          <h1>{t("title")}</h1>
        </div>
        <div className="status-row">
          <p className="page-summary">{t("summary", { count: artifacts.length })}</p>
          <button className="button" onClick={() => void handleRebuildIndex()} type="button">
            {t("rebuild_index")}
          </button>
        </div>
      </header>

      <section className="tool-panel library-upload-panel" aria-label={t("upload_label")}>
        <DocumentUploader onUploaded={() => void loadArtifacts()} />
      </section>

      <section className="library-browser" aria-labelledby="artifact-list-heading">
        <aside className="library-categories" aria-label={t("categories.title")}>
          <p className="eyebrow">{t("categories.title")}</p>
          <div className="library-category-list">
            {categoryItems.map((item) => (
              <button
                aria-label={`${item.label} ${item.count}`}
                aria-pressed={selectedKind === item.kind}
                data-active={selectedKind === item.kind}
                key={item.kind || "all"}
                onClick={() => setSelectedKind(item.kind)}
                type="button"
              >
                <span>{item.label}</span>
                <strong>{item.count}</strong>
              </button>
            ))}
          </div>
        </aside>

        <div className="library-files">
          <div className="section-heading library-file-heading">
            <div>
              <p className="eyebrow">{t("browser_eyebrow")}</p>
              <h2 id="artifact-list-heading">{t("browser_title")}</h2>
            </div>
            <div className="filter-row">
              <label className="search-field">
                <Search aria-hidden="true" size={17} />
                <span className="sr-only">{t("keyword_label")}</span>
                <input
                  onChange={(event) => setKeyword(event.target.value)}
                  placeholder={t("keyword_placeholder")}
                  type="search"
                  value={keyword}
                />
              </label>
            </div>
          </div>

          {loading ? <p className="empty-state">{t("loading")}</p> : null}
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
          {message ? (
            <p className="form-success" role="status">
              {message}
            </p>
          ) : null}
          {!loading && !error && filteredArtifacts.length === 0 ? (
            <p className="empty-state">
              {artifacts.length === 0 ? t("empty") : t("empty_filtered")}
            </p>
          ) : null}

          <div className="library-table" role="table" aria-label={t("browser_title")}>
            <div className="library-table-row library-table-head" role="row">
              <span role="columnheader">{t("table.name")}</span>
              <span role="columnheader">{t("table.owner")}</span>
              <span role="columnheader">{t("table.created_at")}</span>
              <span role="columnheader">{t("table.updated_at")}</span>
              <span role="columnheader">{t("table.actions")}</span>
            </div>
            {filteredArtifacts.map((artifact) => (
              <div className="library-table-row" key={artifact.id} role="row">
                <div className="library-file-name" role="cell">
                  <span className="library-file-icon" aria-hidden="true">
                    <FileText size={18} />
                  </span>
                  <div>
                    <Link href={`/library/${artifact.id}`}>{artifact.display_name}</Link>
                    <span>{t(`kinds.${artifact.kind}`, artifact.kind)}</span>
                  </div>
                  <StatusPill
                    label={
                      artifact.recovery_required
                        ? t("common:states.checking")
                        : artifact.index_status
                    }
                    tone={artifact.recovery_required ? "warning" : "success"}
                  />
                </div>
                <span className="library-muted-cell" role="cell">
                  {t(`owners.${artifact.owner}`, artifact.owner)}
                </span>
                <time className="library-muted-cell" dateTime={artifact.created_at} role="cell">
                  {formatDate(artifact.created_at)}
                </time>
                <time className="library-muted-cell" dateTime={artifact.updated_at} role="cell">
                  {formatDate(artifact.updated_at)}
                </time>
                <div className="library-actions" role="cell">
                  <Link
                    aria-label={t("edit_named", { name: artifact.display_name })}
                    className="library-action-button"
                    href={`/library/${artifact.id}`}
                  >
                    <Pencil size={15} aria-hidden="true" />
                    <span>{t("actions.edit")}</span>
                  </Link>
                  <button
                    aria-label={t("delete_named", { name: artifact.display_name })}
                    className="library-action-button library-action-danger"
                    disabled={deletingId === artifact.id}
                    onClick={() => void handleDelete(artifact)}
                    type="button"
                  >
                    <Trash2 size={15} aria-hidden="true" />
                    <span>{t("actions.delete")}</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <aside className="library-preview" aria-label={t("preview_title")}>
          <div>
            <p className="eyebrow">{t("preview_title")}</p>
            <h2>{previewArtifact ? previewArtifact.display_name : t("preview_empty")}</h2>
          </div>
          {previewArtifact ? (
            <div className="library-preview-meta">
              <span className="tag">{t(`kinds.${previewArtifact.kind}`, previewArtifact.kind)}</span>
              <span className="tag">
                {t(`owners.${previewArtifact.owner}`, previewArtifact.owner)}
              </span>
              <span className="tag">{previewArtifact.index_status}</span>
              <span className="tag">{t("revision", { value: previewArtifact.revision })}</span>
              <span className="tag">
                {previewArtifact.allowed_operations.length > 0 ? t("editable") : t("readonly")}
              </span>
            </div>
          ) : null}
          <p className="page-summary">{t("file_count", { count: filteredArtifacts.length })}</p>
        </aside>
      </section>
    </div>
  );
}
