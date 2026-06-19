"use client";

import { FileText } from "lucide-react";
import { useEffect, useState } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { getMemory, getReport, getReports } from "@/lib/api";
import type {
  MemoryKind,
  MemoryResponse,
  ReportDetailResponse,
  ReportRecord,
} from "@/lib/types";

const memoryTabs: Array<{ value: MemoryKind; label: string }> = [
  { value: "weakness", label: "Weakness" },
  { value: "interview_history", label: "Interview history" },
  { value: "learning_profile", label: "Learning profile" },
];

export default function ReviewPage() {
  const [reports, setReports] = useState<ReportRecord[]>([]);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [reportDetail, setReportDetail] = useState<ReportDetailResponse | null>(null);
  const [memory, setMemory] = useState<MemoryResponse | null>(null);
  const [memoryTab, setMemoryTab] = useState<MemoryKind>("weakness");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const requestedReport = new URLSearchParams(window.location.search).get("report");
    Promise.all([getReports(), getMemory()])
      .then(([reportResponse, memoryResponse]) => {
        setReports(reportResponse.reports);
        setMemory(memoryResponse);
        setSelectedReportId(requestedReport ?? reportResponse.reports[0]?.id ?? null);
      })
      .catch((loadError) =>
        setError(loadError instanceof Error ? loadError.message : "Failed to load review data."),
      );
  }, []);

  useEffect(() => {
    if (!selectedReportId) {
      setReportDetail(null);
      return;
    }
    getReport(selectedReportId)
      .then(setReportDetail)
      .catch((loadError) =>
        setError(loadError instanceof Error ? loadError.message : "Failed to load report."),
      );
  }, [selectedReportId]);

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Reports and memory</p>
          <h1>Review</h1>
        </div>
        <p className="page-summary">{reports.length} completed interviews.</p>
      </header>

      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}

      <section className="review-layout" aria-labelledby="reports-heading">
        <div className="report-list">
          <div className="section-heading">
            <div>
              <p className="eyebrow">History</p>
              <h2 id="reports-heading">Reports</h2>
            </div>
          </div>
          {reports.length === 0 ? <p className="empty-state">No reports yet.</p> : null}
          {reports.map((report) => (
            <button
              className="report-list-item"
              data-active={selectedReportId === report.id}
              key={report.id}
              onClick={() => setSelectedReportId(report.id)}
              type="button"
            >
              <FileText aria-hidden="true" size={18} />
              <span>
                <strong>{report.summary}</strong>
                <time dateTime={report.created_at}>
                  {new Date(report.created_at).toLocaleString()}
                </time>
              </span>
            </button>
          ))}
        </div>

        <div className="report-preview">
          {reportDetail ? (
            <MarkdownView content={reportDetail.content} />
          ) : (
            <p className="empty-state">Select a report to preview it.</p>
          )}
        </div>
      </section>

      <section className="page-section" aria-labelledby="memory-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Persistent context</p>
            <h2 id="memory-heading">Memory</h2>
          </div>
          <div className="segmented-control" role="tablist" aria-label="Memory file">
            {memoryTabs.map((tab) => (
              <button
                aria-selected={memoryTab === tab.value}
                data-active={memoryTab === tab.value}
                key={tab.value}
                onClick={() => setMemoryTab(tab.value)}
                role="tab"
                type="button"
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
        <div className="content-surface memory-preview" role="tabpanel">
          <MarkdownView content={memory?.files[memoryTab].content ?? "Loading memory..."} />
        </div>
      </section>
    </div>
  );
}
