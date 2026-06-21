"use client";

import Link from "next/link";
import { Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { DocumentUploader } from "@/components/DocumentUploader";
import { useTranslation } from "@/hooks/useTranslation";
import { StatusPill } from "@/components/StatusPill";
import { getDocuments } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { DocumentRecord } from "@/lib/types";

export default function LibraryPage() {
  const { t, getCurrentLanguage } = useTranslation("library");
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [keyword, setKeyword] = useState("");
  const [tag, setTag] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getDocuments()
      .then((response) => setDocuments(response.documents))
      .catch((loadError) =>
        setError(getErrorMessage(loadError, t, "common:errors.generic_load")),
      )
      .finally(() => setLoading(false));
  }, []);

  const filteredDocuments = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    const normalizedTag = tag.trim().toLowerCase();
    return documents.filter((document) => {
      const matchesKeyword =
        !normalizedKeyword ||
        [document.title, document.summary, document.source_filename]
          .join(" ")
          .toLowerCase()
          .includes(normalizedKeyword);
      const matchesTag =
        !normalizedTag ||
        document.tags.some((documentTag) => documentTag.toLowerCase().includes(normalizedTag));
      return matchesKeyword && matchesTag;
    });
  }, [documents, keyword, tag]);

  function handleUploaded(document: DocumentRecord) {
    setDocuments((current) => [document, ...current.filter((item) => item.id !== document.id)]);
  }

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("eyebrow")}</p>
          <h1>{t("title")}</h1>
        </div>
        <p className="page-summary">{t("summary", { count: documents.length })}</p>
      </header>

      <section className="tool-panel" aria-label="Upload document">
        <DocumentUploader onUploaded={handleUploaded} />
      </section>

      <section className="page-section" aria-labelledby="document-list-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("indexed_eyebrow")}</p>
            <h2 id="document-list-heading">{t("documents_title")}</h2>
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
            <label>
              <span className="sr-only">{t("tag_label")}</span>
              <input
                onChange={(event) => setTag(event.target.value)}
                placeholder={t("tag_placeholder")}
                type="search"
                value={tag}
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
        {!loading && !error && filteredDocuments.length === 0 ? (
          <p className="empty-state">{t("empty")}</p>
        ) : null}

        <div className="document-grid">
          {filteredDocuments.map((document) => (
            <Link className="document-card" href={`/library/${document.id}`} key={document.id}>
              <div className="document-card-heading">
                <div>
                  <p className="document-source">{document.source_filename}</p>
                  <h3>{document.title}</h3>
                </div>
                <StatusPill
                  label={document.index_status}
                  tone={document.index_status === "completed" ? "success" : "warning"}
                />
              </div>
              <p>{document.summary || t("no_summary")}</p>
              <div className="tag-row">
                {document.tags.map((documentTag) => (
                  <span className="tag" key={documentTag}>
                    {documentTag}
                  </span>
                ))}
              </div>
              <time dateTime={document.updated_at}>
                {t("updated", {
                  value: new Date(document.updated_at).toLocaleString(getCurrentLanguage()),
                })}
              </time>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
