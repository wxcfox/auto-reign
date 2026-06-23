"use client";

import { ClipboardList, FileText, Save } from "lucide-react";
import { useEffect, useState } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { MarkdownView } from "@/components/MarkdownView";
import { getMemory, getReport, getReports, recordRealInterviewRecord } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type {
  MemoryKind,
  MemoryResponse,
  RealInterviewRecordResponse,
  ReportDetailResponse,
  ReportRecord,
} from "@/lib/types";

export default function ReviewPage() {
  const { t, getCurrentLanguage } = useTranslation("review");
  const [reports, setReports] = useState<ReportRecord[]>([]);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [reportDetail, setReportDetail] = useState<ReportDetailResponse | null>(null);
  const [memory, setMemory] = useState<MemoryResponse | null>(null);
  const [memoryTab, setMemoryTab] = useState<MemoryKind>("weakness");
  const [recordText, setRecordText] = useState("");
  const [recording, setRecording] = useState(false);
  const [recordResult, setRecordResult] = useState<RealInterviewRecordResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const memoryTabs: Array<{ value: MemoryKind; label: string }> = [
    { value: "weakness", label: t("tabs.weakness") },
    { value: "interview_history", label: t("tabs.interview_history") },
    { value: "learning_profile", label: t("tabs.learning_profile") },
  ];

  useEffect(() => {
    const requestedReport = new URLSearchParams(window.location.search).get("report");
    Promise.all([getReports(), getMemory()])
      .then(([reportResponse, memoryResponse]) => {
        setReports(reportResponse.reports);
        setMemory(memoryResponse);
        setSelectedReportId(requestedReport ?? reportResponse.reports[0]?.id ?? null);
      })
      .catch((loadError) =>
        setError(getErrorMessage(loadError, t, "review:errors.review_load")),
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
        setError(getErrorMessage(loadError, t, "review:errors.report_load")),
    );
  }, [selectedReportId]);

  const submitRealInterviewRecord = async () => {
    const text = recordText.trim();
    if (!text || recording) {
      return;
    }
    setRecording(true);
    setError(null);
    try {
      const language = getCurrentLanguage() === "en" ? "en" : "zh-CN";
      const result = await recordRealInterviewRecord({ text, language });
      setRecordResult(result);
      setRecordText("");
    } catch (submitError) {
      setError(getErrorMessage(submitError, t, "review:errors.real_interview_save"));
    } finally {
      setRecording(false);
    }
  };

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("eyebrow")}</p>
          <h1>{t("title")}</h1>
        </div>
        <p className="page-summary">{t("summary", { count: reports.length })}</p>
      </header>

      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}

      <section className="page-section" aria-labelledby="real-interview-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("real_interview_eyebrow")}</p>
            <h2 id="real-interview-heading">{t("real_interview_title")}</h2>
          </div>
        </div>
        <div className="real-interview-panel">
          <label className="field-label" htmlFor="real-interview-record">
            {t("real_interview_label")}
          </label>
          <textarea
            id="real-interview-record"
            minLength={1}
            onChange={(event) => setRecordText(event.target.value)}
            rows={8}
            value={recordText}
          />
          <div className="button-row">
            <button
              className="button button-primary"
              disabled={!recordText.trim() || recording}
              onClick={submitRealInterviewRecord}
              type="button"
            >
              <Save aria-hidden="true" size={16} />
              {recording ? t("real_interview_saving") : t("real_interview_submit")}
            </button>
          </div>
          {recordResult ? (
            <div className="real-interview-result">
              <div className="real-interview-result-heading">
                <ClipboardList aria-hidden="true" size={18} />
                <strong>{t("real_interview_result_title")}</strong>
              </div>
              <div className="real-interview-result-grid">
                <div>
                  <h3>{t("real_interview_questions")}</h3>
                  <ul>
                    {recordResult.questions.map((question) => (
                      <li key={question}>{question}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h3>{t("real_interview_weak_points")}</h3>
                  <ul>
                    {recordResult.weak_points.map((weakPoint) => (
                      <li key={weakPoint}>{weakPoint}</li>
                    ))}
                  </ul>
                </div>
              </div>
              <div className="tag-row">
                <span className="tag">{recordResult.raw_artifact.relative_path}</span>
                <span className="tag">{recordResult.high_frequency_artifact.relative_path}</span>
                <span className="tag">{recordResult.status_artifact.relative_path}</span>
              </div>
            </div>
          ) : null}
        </div>
      </section>

      <section className="review-layout" aria-labelledby="reports-heading">
        <div className="report-list">
          <div className="section-heading">
            <div>
              <p className="eyebrow">{t("history_eyebrow")}</p>
              <h2 id="reports-heading">{t("reports_title")}</h2>
            </div>
          </div>
          {reports.length === 0 ? <p className="empty-state">{t("reports_empty")}</p> : null}
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
                  {new Date(report.created_at).toLocaleString(getCurrentLanguage())}
                </time>
              </span>
            </button>
          ))}
        </div>

        <div className="report-preview">
          {reportDetail ? (
            <MarkdownView content={reportDetail.content} />
          ) : (
            <p className="empty-state">{t("select_report")}</p>
          )}
        </div>
      </section>

      <section className="page-section" aria-labelledby="memory-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("memory_eyebrow")}</p>
            <h2 id="memory-heading">{t("memory_title")}</h2>
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
          <MarkdownView content={memory?.files[memoryTab].content ?? t("memory_loading")} />
        </div>
      </section>
    </div>
  );
}
