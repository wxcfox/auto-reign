"use client";

import { BookOpen, Database, FileText, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { StatusPill } from "@/components/StatusPill";
import { getHealth, getWorkspaceArtifacts, getWorkspaceStatus } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { HealthResponse, WorkspaceArtifactSummary, WorkspaceStatusResponse } from "@/lib/types";
import { useTranslation } from "@/hooks/useTranslation";

export default function DashboardPage() {
  const { t } = useTranslation("dashboard");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceStatusResponse | null>(null);
  const [artifacts, setArtifacts] = useState<WorkspaceArtifactSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([getHealth(), getWorkspaceStatus(), getWorkspaceArtifacts()])
      .then(([healthResult, workspaceResult, artifactsResult]) => {
        if (healthResult.status === "fulfilled") {
          setHealth(healthResult.value);
        }
        if (workspaceResult.status === "fulfilled") {
          setWorkspace(workspaceResult.value);
        }
        if (artifactsResult.status === "fulfilled") {
          setArtifacts(artifactsResult.value.artifacts);
        }
        if ([healthResult, workspaceResult, artifactsResult].some((result) => result.status === "rejected")) {
          const firstRejected = [healthResult, workspaceResult, artifactsResult].find(
            (result) => result.status === "rejected",
          );
          setError(
            firstRejected?.status === "rejected"
              ? getErrorMessage(firstRejected.reason, t, "common:errors.generic_load")
              : "加载失败",
          );
        }
      })
      .finally(() => setLoading(false));
  }, []);

  const counts = useMemo(
    () => ({
      total: artifacts.length,
      source: artifacts.filter((artifact) => artifact.kind === "source").length,
      knowledge: artifacts.filter((artifact) => artifact.kind === "knowledge").length,
      practice: artifacts.filter((artifact) => artifact.kind === "practice").length,
      stale: artifacts.filter((artifact) => artifact.index_status === "stale").length,
    }),
    [artifacts],
  );

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Auto Reign</p>
          <h1>面试学习工作台</h1>
        </div>
        <StatusPill
          label={health?.status === "ok" && workspace?.initialized ? "Ready" : loading ? "Checking" : "Unavailable"}
          tone={health?.status === "ok" && workspace?.initialized ? "success" : "warning"}
        />
      </header>

      {error ? <p className="form-error" role="alert">{error}</p> : null}

      <section className="metric-grid" aria-label="Workspace summary">
        <div className="metric">
          <Database aria-hidden="true" size={20} />
          <div>
            <strong>{counts.total}</strong>
            <span>资料总数</span>
          </div>
        </div>
        <div className="metric">
          <Upload aria-hidden="true" size={20} />
          <div>
            <strong>{counts.source}</strong>
            <span>原始资料</span>
          </div>
        </div>
        <div className="metric">
          <BookOpen aria-hidden="true" size={20} />
          <div>
            <strong>{counts.knowledge}</strong>
            <span>知识卡片</span>
          </div>
        </div>
        <div className="metric">
          <FileText aria-hidden="true" size={20} />
          <div>
            <strong>{counts.practice}</strong>
            <span>练习记录</span>
          </div>
        </div>
        <div className="metric">
          <Database aria-hidden="true" size={20} />
          <div>
            <strong>{counts.stale}</strong>
            <span>待索引</span>
          </div>
        </div>
      </section>
    </div>
  );
}
