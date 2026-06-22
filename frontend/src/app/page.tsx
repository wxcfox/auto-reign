"use client";

import { ArrowRight, BookOpen, FileText, MessageSquareText, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { StatusPill } from "@/components/StatusPill";
import { getHealth, getWorkspaceArtifact, getWorkspaceArtifacts, getWorkspaceStatus } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { HealthResponse, WorkspaceArtifactDetail, WorkspaceArtifactSummary, WorkspaceStatusResponse } from "@/lib/types";
import { useTranslation } from "@/hooks/useTranslation";

export default function DashboardPage() {
  const { t } = useTranslation("dashboard");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceStatusResponse | null>(null);
  const [artifacts, setArtifacts] = useState<WorkspaceArtifactSummary[]>([]);
  const [plan, setPlan] = useState<WorkspaceArtifactDetail | null>(null);
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
          const planArtifact = artifactsResult.value.artifacts.find((artifact) => artifact.kind === "plan");
          if (planArtifact) {
            void getWorkspaceArtifact(planArtifact.id).then(setPlan);
          }
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
      source: artifacts.filter((artifact) => artifact.kind === "source").length,
      knowledge: artifacts.filter((artifact) => artifact.kind === "knowledge").length,
      practice: artifacts.filter((artifact) => artifact.kind === "practice").length,
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
      </section>

      <section className="dashboard-grid">
        <div className="page-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">下一步</p>
              <h2>当前计划</h2>
            </div>
            <Link className="text-link" href="/review">
              查看复盘
              <ArrowRight aria-hidden="true" size={16} />
            </Link>
          </div>
          <div className="content-surface">
            <MarkdownView content={plan?.body ?? "# 当前计划\n\n- 上传资料后完成一次推荐面试。"} />
          </div>
        </div>

        <div className="page-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">核心路径</p>
              <h2>上传 {"->"} 面试 {"->"} 复盘</h2>
            </div>
          </div>
          <div className="content-surface latest-report">
            <p>只需要持续上传资料和完成面试，系统会维护知识、练习证据、掌握状态和计划。</p>
          </div>
        </div>
      </section>

      <section className="quick-actions" aria-label="Primary actions">
        <Link className="action-link" href="/library">
          <Upload aria-hidden="true" size={20} />
          <div>
            <strong>上传资料</strong>
            <span>简历、JD、学习笔记都可以直接上传。</span>
          </div>
          <ArrowRight aria-hidden="true" size={18} />
        </Link>
        <Link className="action-link" href="/interview">
          <MessageSquareText aria-hidden="true" size={20} />
          <div>
            <strong>开始面试</strong>
            <span>根据当前资料和练习状态进行针对性训练。</span>
          </div>
          <ArrowRight aria-hidden="true" size={18} />
        </Link>
      </section>
    </div>
  );
}
