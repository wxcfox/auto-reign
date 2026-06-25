"use client";

import Link from "next/link";
import { FileText, PanelLeftClose, PanelLeftOpen, Pencil, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { DocumentUploader } from "@/components/DocumentUploader";
import { StatusPill } from "@/components/StatusPill";
import { useTranslation } from "@/hooks/useTranslation";
import {
  deleteWorkspaceArtifact,
  getWorkspaceArtifacts,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { WorkspaceArtifactSummary } from "@/lib/types";

const CATEGORY_ORDER = [
  "knowledge",
  "question_bank",
  "project",
  "high_frequency",
  "interview_record",
  "source",
  "candidate_profile",
  "target_profile",
  "practice",
  "report",
  "mastery",
  "extracted",
];
const HIDDEN_KINDS = new Set(["plan"]);

export default function LibraryPage() {
  const { getCurrentLanguage, t } = useTranslation("library");
  const [artifacts, setArtifacts] = useState<WorkspaceArtifactSummary[]>([]);
  const [keyword, setKeyword] = useState("");
  const [selectedKind, setSelectedKind] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [categoriesCollapsed, setCategoriesCollapsed] = useState(false);

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
      if (HIDDEN_KINDS.has(artifact.kind)) {
        return false;
      }
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
    const visibleArtifacts = artifacts.filter((artifact) => !HIDDEN_KINDS.has(artifact.kind));
    const counts = visibleArtifacts.reduce<Record<string, number>>((current, artifact) => {
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
      { kind: "", label: t("categories.all"), count: visibleArtifacts.length },
      ...orderedKinds.map((item) => ({
        kind: item,
        label: t(`kinds.${item}`, item),
        count: counts[item] ?? 0,
      })),
    ];
  }, [artifacts, t]);

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
        </div>
      </header>

      <section
        className="library-browser"
        data-categories-collapsed={categoriesCollapsed}
        aria-labelledby="artifact-list-heading"
      >
        <aside
          className="library-sidebar"
          data-collapsed={categoriesCollapsed}
          aria-label={t("categories.title")}
        >
          <div className="library-sidebar-header">
            {categoriesCollapsed ? null : <p className="eyebrow">{t("categories.title")}</p>}
            <button
              aria-label={
                categoriesCollapsed ? t("categories.expand") : t("categories.collapse")
              }
              className="library-sidebar-toggle"
              onClick={() => setCategoriesCollapsed((current) => !current)}
              title={categoriesCollapsed ? t("categories.expand") : t("categories.collapse")}
              type="button"
            >
              {categoriesCollapsed ? (
                <PanelLeftOpen size={16} aria-hidden="true" />
              ) : (
                <PanelLeftClose size={16} aria-hidden="true" />
              )}
            </button>
          </div>
          {categoriesCollapsed ? null : (
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
          )}
        </aside>

        <div className="library-files">
          <div className="section-heading library-file-heading">
            <div>
              <p className="eyebrow">{t("browser_eyebrow")}</p>
              <h2 id="artifact-list-heading">{t("browser_title")}</h2>
            </div>
            <div className="filter-row library-file-actions">
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
              <DocumentUploader onUploaded={() => void loadArtifacts()} />
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

          <div className="library-table-scroll">
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
                      <FileText size={16} />
                    </span>
                    <div>
                      <Link href={`/library/${artifact.id}`} title={artifact.display_name}>
                        {artifact.display_name}
                      </Link>
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
                      title={t("actions.edit")}
                    >
                      <Pencil size={15} aria-hidden="true" />
                      <span className="sr-only">{t("actions.edit")}</span>
                    </Link>
                    <button
                      aria-label={t("delete_named", { name: artifact.display_name })}
                      className="library-action-button library-action-danger"
                      disabled={deletingId === artifact.id}
                      onClick={() => void handleDelete(artifact)}
                      title={t("actions.delete")}
                      type="button"
                    >
                      <Trash2 size={15} aria-hidden="true" />
                      <span className="sr-only">{t("actions.delete")}</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
