"use client";

import { ArrowRight, BookOpen, Database, MessageSquareText, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { StatusPill } from "@/components/StatusPill";
import { getDocuments, getHealth, getMemory, getReports } from "@/lib/api";
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
          setError("Some dashboard data could not be loaded.");
        }
      })
      .finally(() => setLoading(false));
  }, []);

  const availableProviders = useMemo(
    () => Object.entries(health?.providers ?? {}).filter(([, available]) => available),
    [health],
  );
  const weaknessSummary = currentWeaknessSummary(
    memory?.files.weakness.content ?? "No completed interviews yet.",
  );
  const latestReport = reports[0];

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Local interview workbench</p>
          <h1>Dashboard</h1>
        </div>
        <StatusPill
          label={health?.status === "ok" ? "Backend ready" : loading ? "Checking" : "Unavailable"}
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
            <span>Indexed documents</span>
          </div>
        </div>
        <div className="metric">
          <MessageSquareText aria-hidden="true" size={20} />
          <div>
            <strong>{availableProviders.length}</strong>
            <span>Available providers</span>
          </div>
        </div>
        <div className="metric">
          <BookOpen aria-hidden="true" size={20} />
          <div>
            <strong>{reports.length}</strong>
            <span>Interview reports</span>
          </div>
        </div>
      </section>

      <section className="dashboard-grid">
        <div className="page-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Current memory</p>
              <h2>Weakness summary</h2>
            </div>
            <Link className="text-link" href="/review">
              Review memory
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
              <p className="eyebrow">Latest output</p>
              <h2>Interview report</h2>
            </div>
          </div>
          <div className="content-surface latest-report">
            {latestReport ? (
              <>
                <div>
                  <h3>{latestReport.summary}</h3>
                  <time dateTime={latestReport.created_at}>
                    {new Date(latestReport.created_at).toLocaleString()}
                  </time>
                </div>
                <Link className="button" href={`/review?report=${latestReport.id}`}>
                  Open report
                  <ArrowRight aria-hidden="true" size={16} />
                </Link>
              </>
            ) : (
              <p className="empty-state">No interview reports yet.</p>
            )}
          </div>
        </div>
      </section>

      <section className="quick-actions" aria-label="Primary actions">
        <Link className="action-link" href="/library">
          <Upload aria-hidden="true" size={20} />
          <div>
            <strong>Upload documents</strong>
            <span>Add Markdown or TXT knowledge sources.</span>
          </div>
          <ArrowRight aria-hidden="true" size={18} />
        </Link>
        <Link className="action-link" href="/interview">
          <MessageSquareText aria-hidden="true" size={20} />
          <div>
            <strong>Start interview</strong>
            <span>Configure a role and begin a mock session.</span>
          </div>
          <ArrowRight aria-hidden="true" size={18} />
        </Link>
      </section>
    </div>
  );
}
