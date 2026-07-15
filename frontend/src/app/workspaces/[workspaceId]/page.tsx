"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { WorkspaceBrowser } from "@/components/WorkspaceBrowser";
import { useTranslation } from "@/hooks/useTranslation";

type WorkspacePageProps = {
  params: Promise<{ workspaceId: string }>;
};

export default function WorkspacePage({ params }: WorkspacePageProps) {
  const { t } = useTranslation("workspaces");
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setWorkspaceId(null);
    void params.then((resolved) => {
      if (active) {
        setWorkspaceId(resolved.workspaceId);
      }
    });
    return () => {
      active = false;
    };
  }, [params]);

  if (workspaceId === null) {
    return (
      <main className="page-stack">
        <p className="workspace-state" role="status">
          {t("page.loading")}
        </p>
      </main>
    );
  }

  return (
    <main className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Agent Home</p>
          <h1>{t("page.homeTitle")}</h1>
          <p className="page-summary">{t("page.homeDescription")}</p>
        </div>
        <Link className="text-link" href="/workspaces">
          {t("page.back")}
        </Link>
      </header>
      <WorkspaceBrowser scope="private" workspaceId={workspaceId} />
    </main>
  );
}
