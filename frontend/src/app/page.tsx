"use client";

import { ArrowRight, BookOpen, Database, MessageSquareText, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { MarkdownView } from "@/components/MarkdownView";
import { StatusPill } from "@/components/StatusPill";
import { getDocuments, getHealth, getMemory, getReports } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type {
  DocumentRecord,
  HealthResponse,
  MemoryResponse,
  ReportRecord,
} from "@/lib/types";

function currentWeaknessSummary(content: string): string {
  const heading = "## Current Weakness Summary";
  if (!content.includes(heading)) {
    return content;
  }
  return content.split(heading, 2)[1].split("\n## ", 1)[0].trim();
}

export default function DashboardPage() {
  const { t, getCurrentLanguage } = useTranslation("dashboard");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [memory, setMemory] = useState<MemoryResponse | null>(null);
  const [reports, setReports] = useState<ReportRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([getHealth(), getDocuments(), getMemory(), getReports()])
      .then(([healthResult, documentsResult, memoryResult, reportsResult]) => {
        if (healthResult.status === "fulfilled") {
          setHealth(healthResult.value);
        }
        if (documentsResult.status === "fulfilled") {
          setDocuments(documentsResult.value.documents);
        }
        if (memoryResult.status === "fulfilled") {
          setMemory(memoryResult.value);
        }
        if (reportsResult.status === "fulfilled") {
          setReports(reportsResult.value.reports);
        }
        if (
          [healthResult, documentsResult, memoryResult, reportsResult].some(
            (result) => result.status === "rejected",
          )
        ) {
          const firstRejected = [healthResult, documentsResult, memoryResult, reportsResult].find(
            (result) => result.status === "rejected",
          );
          setError(
            firstRejected?.status === "rejected"
              ? getErrorMessage(firstRejected.reason, t, "common:errors.generic_load")
              : t("status_error"),
          );
        }
      })
      .finally(() => setLoading(false));
  }, []);

  const availableProviders = useMemo(
    () => Object.entries(health?.providers ?? {}).filter(([, available]) => available),
    [health],
  );
  const weaknessSummary = currentWeaknessSummary(
    memory?.files.weakness.content ?? t("memory.empty"),
  );
  const latestReport = reports[0];

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("eyebrow")}</p>
          <h1>{t("title")}</h1>
        </div>
        <StatusPill
          label={
            health?.status === "ok"
              ? t("status_ready")
              : loading
                ? t("common:states.checking")
                : t("common:states.unavailable")
          }
          tone={health?.status === "ok" ? "success" : "warning"}
        />
      </header>

      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}

      <section className="metric-grid" aria-label="System summary">
        <div className="metric">
          <Database aria-hidden="true" size={20} />
          <div>
            <strong>{documents.length}</strong>
            <span>{t("metrics.documents")}</span>
          </div>
        </div>
        <div className="metric">
          <MessageSquareText aria-hidden="true" size={20} />
          <div>
            <strong>{availableProviders.length}</strong>
            <span>{t("metrics.providers")}</span>
          </div>
        </div>
        <div className="metric">
          <BookOpen aria-hidden="true" size={20} />
          <div>
            <strong>{reports.length}</strong>
            <span>{t("metrics.reports")}</span>
          </div>
        </div>
      </section>

      <section className="dashboard-grid">
        <div className="page-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">{t("memory.eyebrow")}</p>
              <h2>{t("memory.title")}</h2>
            </div>
            <Link className="text-link" href="/review">
              {t("common:actions.review_memory")}
              <ArrowRight aria-hidden="true" size={16} />
            </Link>
          </div>
          <div className="content-surface">
            <MarkdownView content={weaknessSummary} />
          </div>
        </div>

        <div className="page-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">{t("report.eyebrow")}</p>
              <h2>{t("report.title")}</h2>
            </div>
          </div>
          <div className="content-surface latest-report">
            {latestReport ? (
              <>
                <div>
                  <h3>{latestReport.summary}</h3>
                  <time dateTime={latestReport.created_at}>
                    {new Date(latestReport.created_at).toLocaleString(getCurrentLanguage())}
                  </time>
                </div>
                <Link className="button" href={`/review?report=${latestReport.id}`}>
                  {t("common:actions.open_report")}
                  <ArrowRight aria-hidden="true" size={16} />
                </Link>
              </>
            ) : (
              <p className="empty-state">{t("report.empty")}</p>
            )}
          </div>
        </div>
      </section>

      <section className="quick-actions" aria-label="Primary actions">
        <Link className="action-link" href="/library">
          <Upload aria-hidden="true" size={20} />
          <div>
            <strong>{t("actions.upload_title")}</strong>
            <span>{t("actions.upload_desc")}</span>
          </div>
          <ArrowRight aria-hidden="true" size={18} />
        </Link>
        <Link className="action-link" href="/interview">
          <MessageSquareText aria-hidden="true" size={20} />
          <div>
            <strong>{t("actions.interview_title")}</strong>
            <span>{t("actions.interview_desc")}</span>
          </div>
          <ArrowRight aria-hidden="true" size={18} />
        </Link>
      </section>
    </div>
  );
}
