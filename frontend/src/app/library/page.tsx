"use client";

import Link from "next/link";
import { Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { DocumentUploader } from "@/components/DocumentUploader";
import { StatusPill } from "@/components/StatusPill";
import { useTranslation } from "@/hooks/useTranslation";
import { getWorkspaceArtifacts, rebuildWorkspaceIndex } from "@/lib/api";
import { getErrorMessage } from "@/lib/error-messages";
import type { WorkspaceArtifactSummary } from "@/lib/types";

function labelForKind(kind: string): string {
  const labels: Record<string, string> = {
    source: "原始资料",
    extracted: "提取文本",
    candidate_profile: "候选人画像",
    target_profile: "目标岗位",
    knowledge: "知识卡片",
    practice: "练习记录",
    mastery: "掌握状态",
    plan: "当前计划",
    report: "复盘报告",
  };
  return labels[kind] ?? kind;
}

export default function LibraryPage() {
  const { t } = useTranslation("library");
  const [artifacts, setArtifacts] = useState<WorkspaceArtifactSummary[]>([]);
  const [keyword, setKeyword] = useState("");
  const [kind, setKind] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  function loadArtifacts() {
    setLoading(true);
    getWorkspaceArtifacts()
      .then((response) => setArtifacts(response.artifacts))
      .catch((loadError) =>
        setError(getErrorMessage(loadError, t, "common:errors.generic_load")),
      )
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    loadArtifacts();
  }, []);

  const filteredArtifacts = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return artifacts.filter((artifact) => {
      const matchesKeyword =
        !normalizedKeyword ||
        `${artifact.relative_path} ${artifact.kind}`.toLowerCase().includes(normalizedKeyword);
      const matchesKind = !kind || artifact.kind === kind;
      return matchesKeyword && matchesKind;
    });
  }, [artifacts, keyword, kind]);

  const kinds = Array.from(new Set(artifacts.map((artifact) => artifact.kind))).sort();

  async function handleRebuildIndex() {
    setError(null);
    setMessage(null);
    try {
      await rebuildWorkspaceIndex();
      await getWorkspaceArtifacts().then((response) => setArtifacts(response.artifacts));
      setMessage("索引已重建");
    } catch (rebuildError) {
      setError(getErrorMessage(rebuildError, t, "common:errors.generic_save"));
    }
  }

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">学习资料</p>
          <h1>资料库</h1>
        </div>
        <div className="status-row">
          <p className="page-summary">已整理 {artifacts.length} 个学习文件</p>
          <button className="button" onClick={() => void handleRebuildIndex()} type="button">
            重建索引
          </button>
        </div>
      </header>

      <section className="tool-panel" aria-label="Upload material">
        <DocumentUploader onUploaded={() => loadArtifacts()} />
      </section>

      <section className="page-section" aria-labelledby="artifact-list-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">自动整理</p>
            <h2 id="artifact-list-heading">学习文件</h2>
          </div>
          <div className="filter-row">
            <label className="search-field">
              <Search aria-hidden="true" size={17} />
              <span className="sr-only">搜索</span>
              <input
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索路径或类型"
                type="search"
                value={keyword}
              />
            </label>
            <label>
              <span className="sr-only">类型</span>
              <select onChange={(event) => setKind(event.target.value)} value={kind}>
                <option value="">全部类型</option>
                {kinds.map((item) => (
                  <option key={item} value={item}>
                    {labelForKind(item)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        {loading ? <p className="empty-state">加载中...</p> : null}
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
        {!loading && !error && filteredArtifacts.length === 0 ? (
          <p className="empty-state">上传简历、JD 或学习笔记后，系统会自动整理到这里。</p>
        ) : null}

        <div className="document-grid">
          {filteredArtifacts.map((artifact) => (
            <Link className="document-card" href={`/library/${artifact.id}`} key={artifact.id}>
              <div className="document-card-heading">
                <div>
                  <p className="document-source">{labelForKind(artifact.kind)}</p>
                  <h3>{artifact.relative_path}</h3>
                </div>
                <StatusPill
                  label={artifact.recovery_required ? "需要确认" : artifact.processing_status}
                  tone={artifact.recovery_required ? "warning" : "success"}
                />
              </div>
              <p>revision {artifact.revision}</p>
              <div className="tag-row">
                <span className="tag">{artifact.index_status}</span>
                {artifact.allowed_operations.length > 0 ? (
                  <span className="tag">可编辑</span>
                ) : (
                  <span className="tag">只读</span>
                )}
              </div>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
