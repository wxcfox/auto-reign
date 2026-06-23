"use client";

import { BookOpen, Database, FileText, PlayCircle, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { StatusPill } from "@/components/StatusPill";
import { getHealth, getPreparationTasks, getWorkspaceArtifacts, getWorkspaceStatus } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type {
  HealthResponse,
  PreparationTask,
  WorkspaceArtifactSummary,
  WorkspaceStatusResponse,
} from "@/lib/types";
import { useTranslation } from "@/hooks/useTranslation";

export default function DashboardPage() {
  const { t } = useTranslation("dashboard");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceStatusResponse | null>(null);
  const [artifacts, setArtifacts] = useState<WorkspaceArtifactSummary[]>([]);
  const [preparationTasks, setPreparationTasks] = useState<PreparationTask[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([
      getHealth(),
      getWorkspaceStatus(),
      getWorkspaceArtifacts(),
      getPreparationTasks(),
    ])
      .then(([healthResult, workspaceResult, artifactsResult, tasksResult]) => {
        if (healthResult.status === "fulfilled") {
          setHealth(healthResult.value);
        }
        if (workspaceResult.status === "fulfilled") {
          setWorkspace(workspaceResult.value);
        }
        if (artifactsResult.status === "fulfilled") {
          setArtifacts(artifactsResult.value.artifacts);
        }
        if (tasksResult.status === "fulfilled") {
          setPreparationTasks(tasksResult.value.tasks);
        }
        if ([healthResult, workspaceResult, artifactsResult, tasksResult].some((result) => result.status === "rejected")) {
          const firstRejected = [healthResult, workspaceResult, artifactsResult, tasksResult].find(
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

      <section className="prep-panel" aria-labelledby="prep-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{t("prep.eyebrow")}</p>
            <h2 id="prep-heading">{t("prep.title")}</h2>
          </div>
          <Link className="button button-primary" href="/interview">
            <PlayCircle aria-hidden="true" size={17} />
            {t("prep.start_drill")}
          </Link>
        </div>
        {preparationTasks.length === 0 ? (
          <p className="empty-state">{t("prep.empty")}</p>
        ) : (
          <ol className="prep-task-list">
            {preparationTasks.map((task, index) => (
              <li className="prep-task" key={`${task.title}-${index}`}>
                <span className="prep-task-index">{index + 1}</span>
                <div className="prep-task-copy">
                  <p>{task.title}</p>
                  <small>{task.reason}</small>
                </div>
                <div className="prep-task-actions">
                  <Link className="button button-primary" href="/interview">
                    <PlayCircle aria-hidden="true" size={16} />
                    {t("prep.start_drill")}
                  </Link>
                  {task.source_artifact_id ? (
                    <Link className="button" href={`/library/${task.source_artifact_id}`}>
                      <FileText aria-hidden="true" size={16} />
                      {t("prep.view_status")}
                    </Link>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

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
