"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { KnowledgeDocumentTable } from "@/components/KnowledgeDocumentTable";
import { KnowledgeUploader } from "@/components/KnowledgeUploader";
import { useTranslation } from "@/hooks/useTranslation";
import { getKnowledgeCollection } from "@/lib/api";
import type { KnowledgeCollection } from "@/lib/types";

type KnowledgeCollectionPageProps = {
  params: Promise<{ collectionId: string }>;
};

export default function KnowledgeCollectionPage({ params }: KnowledgeCollectionPageProps) {
  const { t } = useTranslation("knowledge");
  const [collectionId, setCollectionId] = useState<string | null>(null);
  const [collection, setCollection] = useState<KnowledgeCollection | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [retryVersion, setRetryVersion] = useState(0);
  const [documentVersion, setDocumentVersion] = useState(0);

  useEffect(() => {
    let active = true;
    setCollectionId(null);
    setCollection(null);
    setLoading(true);
    setLoadError(false);
    void params
      .then(async (resolved) => {
        if (!active) {
          return;
        }
        setCollectionId(resolved.collectionId);
        return getKnowledgeCollection(resolved.collectionId);
      })
      .then((loaded) => {
        if (active && loaded) {
          setCollection(loaded);
        }
      })
      .catch(() => {
        if (active) {
          setLoadError(true);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [params, retryVersion]);

  if (loading) {
    return (
      <main className="page-stack knowledge-page">
        <p className="knowledge-state" role="status">
          {t("page.loading")}
        </p>
      </main>
    );
  }

  if (loadError || !collection || !collectionId) {
    return (
      <main className="page-stack knowledge-page">
        <div className="knowledge-state knowledge-state--error">
          <p role="alert">{t("page.loadError")}</p>
          <button onClick={() => setRetryVersion((current) => current + 1)} type="button">
            {t("actions.retry")}
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="page-stack knowledge-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("page.eyebrow")}</p>
          <h1>{collection.name}</h1>
          <p className="page-summary">{t("page.detailDescription")}</p>
        </div>
        <Link className="text-link" href="/knowledge">
          {t("page.back")}
        </Link>
      </header>

      {collection.can_manage ? (
        <section className="page-section tool-panel" aria-labelledby="knowledge-upload-heading">
          <h2 className="sr-only" id="knowledge-upload-heading">
            {t("uploader.title")}
          </h2>
          <KnowledgeUploader
            collectionId={collectionId}
            onUploaded={() => setDocumentVersion((current) => current + 1)}
          />
        </section>
      ) : null}

      <section className="page-section" aria-labelledby="knowledge-documents-title">
        <div className="section-heading">
          <h2 id="knowledge-documents-title">{t("documents.title")}</h2>
        </div>
        <KnowledgeDocumentTable
          canManage={collection.can_manage}
          collectionId={collectionId}
          key={documentVersion}
        />
      </section>
    </main>
  );
}
