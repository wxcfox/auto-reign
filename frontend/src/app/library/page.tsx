"use client";

import Link from "next/link";
import { Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { DocumentUploader } from "@/components/DocumentUploader";
import { StatusPill } from "@/components/StatusPill";
import { getDocuments } from "@/lib/api";
import type { DocumentRecord } from "@/lib/types";

export default function LibraryPage() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [keyword, setKeyword] = useState("");
  const [tag, setTag] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getDocuments()
      .then((response) => setDocuments(response.documents))
      .catch((loadError) =>
        setError(loadError instanceof Error ? loadError.message : "Failed to load documents."),
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
          <p className="eyebrow">Knowledge base</p>
          <h1>Library</h1>
        </div>
        <p className="page-summary">{documents.length} documents available for interview context.</p>
      </header>

      <section className="tool-panel" aria-label="Upload document">
        <DocumentUploader onUploaded={handleUploaded} />
      </section>

      <section className="page-section" aria-labelledby="document-list-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Indexed material</p>
            <h2 id="document-list-heading">Documents</h2>
          </div>
          <div className="filter-row">
            <label className="search-field">
              <Search aria-hidden="true" size={17} />
              <span className="sr-only">Filter by keyword</span>
              <input
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="Keyword"
                type="search"
                value={keyword}
              />
            </label>
            <label>
              <span className="sr-only">Filter by tag</span>
              <input
                onChange={(event) => setTag(event.target.value)}
                placeholder="Tag"
                type="search"
                value={tag}
              />
            </label>
          </div>
        </div>

        {loading ? <p className="empty-state">Loading documents...</p> : null}
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        {!loading && !error && filteredDocuments.length === 0 ? (
          <p className="empty-state">No documents match the current filters.</p>
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
              <p>{document.summary || "No summary available."}</p>
              <div className="tag-row">
                {document.tags.map((documentTag) => (
                  <span className="tag" key={documentTag}>
                    {documentTag}
                  </span>
                ))}
              </div>
              <time dateTime={document.updated_at}>
                Updated {new Date(document.updated_at).toLocaleString()}
              </time>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
