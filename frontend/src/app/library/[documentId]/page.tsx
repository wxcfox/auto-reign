"use client";

import { ArrowLeft, RefreshCw, Save } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { StatusPill } from "@/components/StatusPill";
import { getDocument, reindexDocument, updateDocument } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { DocumentRecord, DocumentUpdate } from "@/lib/types";

type FormState = {
  title: string;
  summary: string;
  tags: string;
  knowledgePoints: string;
  weaknessCandidates: string;
};

const emptyForm: FormState = {
  title: "",
  summary: "",
  tags: "",
  knowledgePoints: "",
  weaknessCandidates: "",
};

function toFormState(document: DocumentRecord): FormState {
  return {
    title: document.title,
    summary: document.summary,
    tags: document.tags.join("\n"),
    knowledgePoints: document.knowledge_points.join("\n"),
    weaknessCandidates: document.weakness_candidates.join("\n"),
  };
}

function toList(value: string): string[] {
  return value
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export default function DocumentDetailPage() {
  const { t } = useTranslation("library");
  const params = useParams<{ documentId: string }>();
  const documentId = params.documentId;
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getDocument(documentId)
      .then((response) => {
        setDocument(response);
        setForm(toFormState(response));
      })
      .catch((loadError) =>
        setError(getErrorMessage(loadError, t, "common:errors.generic_load")),
      );
  }, [documentId]);

  function setField(field: keyof FormState, value: string) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function buildUpdate(): DocumentUpdate {
    return {
      title: form.title.trim(),
      summary: form.summary.trim(),
      tags: toList(form.tags),
      knowledge_points: toList(form.knowledgePoints),
      weakness_candidates: toList(form.weaknessCandidates),
    };
  }

  async function saveDocument(reindex: boolean) {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      let saved = await updateDocument(documentId, buildUpdate());
      if (reindex) {
        saved = await reindexDocument(documentId);
      }
      setDocument(saved);
      setForm(toFormState(saved));
      setMessage(reindex ? t("detail.save_reindex_success") : t("detail.save_success"));
    } catch (saveError) {
      setError(getErrorMessage(saveError, t, "common:errors.generic_save"));
    } finally {
      setSaving(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void saveDocument(false);
  }

  if (!document && !error) {
    return <p className="empty-state">{t("detail.loading")}</p>;
  }

  return (
    <div className="page-stack">
      <Link className="back-link" href="/library">
        <ArrowLeft aria-hidden="true" size={17} />
        {t("detail.back")}
      </Link>

      {error && !document ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}

      {document ? (
        <>
          <header className="page-header">
            <div>
              <p className="eyebrow">{document.source_filename}</p>
              <h1>{document.title}</h1>
            </div>
            <div className="status-row">
              <StatusPill
                label={t("detail.source_analysis", { status: document.analysis_status })}
                tone={document.analysis_status === "completed" ? "success" : "warning"}
              />
              <StatusPill
                label={t("detail.source_index", { status: document.index_status })}
                tone={document.index_status === "completed" ? "success" : "warning"}
              />
            </div>
          </header>

          <form className="editor-form" onSubmit={handleSubmit}>
            <label>
              <span className="field-label">{t("detail.title_label")}</span>
              <input
                onChange={(event) => setField("title", event.target.value)}
                required
                value={form.title}
              />
            </label>

            <label>
              <span className="field-label">{t("detail.summary_label")}</span>
              <textarea
                onChange={(event) => setField("summary", event.target.value)}
                rows={5}
                value={form.summary}
              />
            </label>

            <div className="editor-grid">
              <label>
                <span className="field-label">{t("detail.tags_label")}</span>
                <textarea
                  onChange={(event) => setField("tags", event.target.value)}
                  rows={7}
                  value={form.tags}
                />
              </label>
              <label>
                <span className="field-label">{t("detail.knowledge_points_label")}</span>
                <textarea
                  onChange={(event) => setField("knowledgePoints", event.target.value)}
                  rows={7}
                  value={form.knowledgePoints}
                />
              </label>
              <label>
                <span className="field-label">{t("detail.weakness_candidates_label")}</span>
                <textarea
                  onChange={(event) => setField("weaknessCandidates", event.target.value)}
                  rows={7}
                  value={form.weaknessCandidates}
                />
              </label>
            </div>

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

            <div className="button-row">
              <button className="button" disabled={saving} type="submit">
                <Save aria-hidden="true" size={17} />
                {t("common:actions.save")}
              </button>
              <button
                className="button button-primary"
                disabled={saving}
                onClick={() => void saveDocument(true)}
                type="button"
              >
                <RefreshCw aria-hidden="true" size={17} />
                {saving ? t("common:states.working") : t("common:actions.save_and_reindex")}
              </button>
            </div>
          </form>
        </>
      ) : null}
    </div>
  );
}
