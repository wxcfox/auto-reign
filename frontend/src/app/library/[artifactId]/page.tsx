"use client";

import { ArrowLeft, Save } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { StatusPill } from "@/components/StatusPill";
import { useTranslation } from "@/hooks/useTranslation";
import { getWorkspaceArtifact, replaceWorkspaceArtifactBody } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { WorkspaceArtifactDetail } from "@/lib/types";

export default function ArtifactDetailPage() {
  const { t } = useTranslation("library");
  const params = useParams<{ artifactId: string }>();
  const artifactId = params.artifactId;
  const [artifact, setArtifact] = useState<WorkspaceArtifactDetail | null>(null);
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getWorkspaceArtifact(artifactId)
      .then((response) => {
        setArtifact(response);
        setBody(response.body ?? "");
      })
      .catch((loadError) =>
        setError(getErrorMessage(loadError, t, "common:errors.generic_load")),
      );
  }, [artifactId]);

  const canReplaceBody = artifact?.allowed_operations.includes("replace_body") ?? false;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!artifact || !canReplaceBody || saving) {
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await replaceWorkspaceArtifactBody(artifact.id, artifact.revision, body);
      const refreshed = await getWorkspaceArtifact(saved.id);
      setArtifact(refreshed);
      setBody(refreshed.body ?? "");
      setMessage(t("detail.save_success"));
    } catch (saveError) {
      setError(getErrorMessage(saveError, t, "common:errors.generic_save"));
    } finally {
      setSaving(false);
    }
  }

  if (!artifact && !error) {
    return <p className="empty-state">{t("detail.loading")}</p>;
  }

  return (
    <div className="page-stack">
      <Link className="back-link" href="/library">
        <ArrowLeft aria-hidden="true" size={17} />
        {t("detail.back")}
      </Link>

      {error && !artifact ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}

      {artifact ? (
        <>
          <header className="page-header">
            <div>
              <p className="eyebrow">{artifact.kind}</p>
              <h1>{artifact.relative_path}</h1>
            </div>
            <div className="status-row">
              <StatusPill
                label={
                  artifact.recovery_required
                    ? t("detail.needs_review")
                    : t(`states.${artifact.processing_status}`, artifact.processing_status)
                }
                tone={artifact.recovery_required ? "warning" : "success"}
              />
              <StatusPill
                label={
                  artifact.allowed_operations.length > 0
                    ? t("detail.editable")
                    : t("detail.read_only")
                }
                tone={artifact.allowed_operations.length > 0 ? "success" : "neutral"}
              />
            </div>
          </header>

          {canReplaceBody ? (
            <form className="editor-form" onSubmit={handleSubmit}>
              <label>
                <span className="field-label">{t("detail.markdown_body")}</span>
                <textarea onChange={(event) => setBody(event.target.value)} rows={18} value={body} />
              </label>
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
                <button className="button button-primary" disabled={saving} type="submit">
                  <Save aria-hidden="true" size={17} />
                  {saving ? t("detail.saving") : t("detail.save")}
                </button>
              </div>
            </form>
          ) : (
            <section className="content-surface">
              {artifact.body ? <MarkdownView content={artifact.body} /> : <p>{t("detail.read_only_body")}</p>}
            </section>
          )}
        </>
      ) : null}
    </div>
  );
}
