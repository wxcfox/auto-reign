"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { renameKnowledgeDocument } from "@/lib/api";
import { MAX_RESOURCE_NAME_LENGTH } from "@/lib/limits";
import type { KnowledgeDocument } from "@/lib/types";

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

export type KnowledgeDocumentRenameDialogProps = {
  collectionId: string;
  document: KnowledgeDocument;
  onClose: () => void;
  onRenamed: (document: KnowledgeDocument) => void;
};

export function KnowledgeDocumentRenameDialog({
  collectionId,
  document: target,
  onClose,
  onRenamed,
}: KnowledgeDocumentRenameDialogProps) {
  const { t } = useTranslation("knowledge");
  const [value, setValue] = useState(target.name);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLFormElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) {
      return;
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!pending) {
          onClose();
        }
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const focusable = dialog!.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusable.length === 0) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = dialog!.ownerDocument.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }
    dialog.addEventListener("keydown", handleKeyDown);
    return () => dialog.removeEventListener("keydown", handleKeyDown);
  }, [onClose, pending]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = value.trim();
    if (!name || pending) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      onRenamed(await renameKnowledgeDocument(collectionId, target.id, name));
    } catch {
      setError(t("documents.renameError"));
      setPending(false);
    }
  }

  return (
    <div className="dialog-backdrop">
      <form
        aria-labelledby="knowledge-rename-title"
        aria-modal="true"
        className="dialog-panel"
        onSubmit={(event) => void handleSubmit(event)}
        ref={dialogRef}
        role="dialog"
      >
        <div className="dialog-heading">
          <h2 id="knowledge-rename-title">{t("documents.renameTitle")}</h2>
          <p>{t("documents.renameDescription")}</p>
        </div>
        <label htmlFor="knowledge-rename-input">{t("documents.nameLabel")}</label>
        <input
          autoFocus
          id="knowledge-rename-input"
          maxLength={MAX_RESOURCE_NAME_LENGTH}
          onChange={(event) => setValue(event.target.value)}
          value={value}
        />
        {error ? (
          <p className="form-error" role="alert">{error}</p>
        ) : null}
        <div className="dialog-actions">
          <button className="button" disabled={pending} onClick={onClose} type="button">
            {t("actions.cancel")}
          </button>
          <button
            className="button button-primary"
            disabled={!value.trim() || pending}
            type="submit"
          >
            {t("actions.save")}
          </button>
        </div>
      </form>
    </div>
  );
}
