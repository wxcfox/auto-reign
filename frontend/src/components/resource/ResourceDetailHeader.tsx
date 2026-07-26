"use client";

import { Pencil } from "lucide-react";
import type { ReactNode } from "react";

export type ResourceDetailHeaderProps = {
  actions?: ReactNode;
  breadcrumb: string;
  editDisabled?: boolean;
  editLabel?: string;
  editText?: string;
  name: string;
  onEdit?: () => void;
  statusBadge: string;
};

export function ResourceDetailHeader({
  actions,
  breadcrumb,
  editDisabled = false,
  editLabel,
  editText,
  name,
  onEdit,
  statusBadge,
}: ResourceDetailHeaderProps) {
  return (
    <header className="resource-detail-header">
      <div className="resource-breadcrumb">
        <span>{breadcrumb}</span>
        <span className="resource-breadcrumb-sep">/</span>
        <h2>{name}</h2>
        <span className="resource-status-badge">{statusBadge}</span>
        {onEdit ? (
          <button
            aria-label={editLabel}
            className="resource-breadcrumb-edit"
            disabled={editDisabled}
            onClick={onEdit}
            type="button"
          >
            <Pencil aria-hidden="true" size={12} />
            {editText}
          </button>
        ) : null}
      </div>
      {actions ? <div className="resource-actions">{actions}</div> : null}
    </header>
  );
}
