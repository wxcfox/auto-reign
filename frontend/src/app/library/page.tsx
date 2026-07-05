"use client";

import Link from "next/link";
import {
  ChevronLeft,
  ChevronRight,
  FileText,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Search,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { MaterialUploader } from "@/components/MaterialUploader";
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
const PAGE_SIZE_OPTIONS = [10, 20, 50];
type PageItem = number | "ellipsis-start" | "ellipsis-end";

function pageItems(currentPage: number, totalPages: number): PageItem[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const items: PageItem[] = [1];
  if (currentPage > 3) {
    items.push("ellipsis-start");
  }

  const start = Math.max(2, currentPage - 1);
  const end = Math.min(totalPages - 1, currentPage + 1);
  for (let page = start; page <= end; page += 1) {
    items.push(page);
  }

  if (currentPage < totalPages - 2) {
    items.push("ellipsis-end");
  }
  items.push(totalPages);
  return items;
}

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
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE_OPTIONS[0]);

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

  const totalPages = Math.max(1, Math.ceil(filteredArtifacts.length / pageSize));
  const paginationVisible = filteredArtifacts.length > pageSize;
  const paginatedArtifacts = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredArtifacts.slice(start, start + pageSize);
  }, [currentPage, filteredArtifacts, pageSize]);
  const paginationItems = useMemo(
    () => pageItems(currentPage, totalPages),
    [currentPage, totalPages],
  );

  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

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

  function updateKeyword(value: string) {
    setKeyword(value);
    setCurrentPage(1);
  }

  function updateSelectedKind(kind: string) {
    setSelectedKind(kind);
    setCurrentPage(1);
  }

  function updatePageSize(value: string) {
    setPageSize(Number(value));
    setCurrentPage(1);
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
                  onClick={() => updateSelectedKind(item.kind)}
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
                  onChange={(event) => updateKeyword(event.target.value)}
                  placeholder={t("keyword_placeholder")}
                  type="search"
                  value={keyword}
                />
              </label>
              <MaterialUploader onUploaded={() => void loadArtifacts()} />
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

          <div className="library-table-shell">
            <div className="library-table-scroll">
              <div className="library-table" role="table" aria-label={t("browser_title")}>
                <div className="library-table-row library-table-head" role="row">
                  <span role="columnheader">{t("table.name")}</span>
                  <span role="columnheader">{t("table.owner")}</span>
                  <span role="columnheader">{t("table.created_at")}</span>
                  <span role="columnheader">{t("table.updated_at")}</span>
                  <span role="columnheader">{t("table.actions")}</span>
                </div>
                {paginatedArtifacts.map((artifact) => (
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
                            : t(`states.${artifact.index_status}`, artifact.index_status)
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
            {paginationVisible ? (
              <nav className="library-pagination-bar" aria-label={t("pagination.label")}>
                <p className="library-pagination-total">
                  {t("pagination.total_count", { count: filteredArtifacts.length })}
                </p>
                <div className="library-pagination-pages">
                  <button
                    aria-label={t("pagination.previous")}
                    className="library-pagination-button"
                    disabled={currentPage <= 1}
                    onClick={() => setCurrentPage((page) => Math.max(1, page - 1))}
                    type="button"
                  >
                    <ChevronLeft size={15} aria-hidden="true" />
                  </button>
                  {paginationItems.map((item) =>
                    typeof item === "number" ? (
                      <button
                        aria-current={item === currentPage ? "page" : undefined}
                        className="library-pagination-button"
                        data-active={item === currentPage}
                        key={item}
                        onClick={() => setCurrentPage(item)}
                        type="button"
                      >
                        {item}
                      </button>
                    ) : (
                      <span className="library-pagination-ellipsis" key={item}>
                        <MoreHorizontal size={15} aria-hidden="true" />
                      </span>
                    ),
                  )}
                  <button
                    aria-label={t("pagination.next")}
                    className="library-pagination-button"
                    disabled={currentPage >= totalPages}
                    onClick={() => setCurrentPage((page) => Math.min(totalPages, page + 1))}
                    type="button"
                  >
                    <ChevronRight size={15} aria-hidden="true" />
                  </button>
                </div>
                <label className="library-page-size">
                  <span>{t("pagination.page_size")}</span>
                  <select
                    aria-label={t("pagination.page_size_label")}
                    onChange={(event) => updatePageSize(event.target.value)}
                    value={pageSize}
                  >
                    {PAGE_SIZE_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
              </nav>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
