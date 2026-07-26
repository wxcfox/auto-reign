"use client";

import { Loader2, Send, Square } from "lucide-react";
import type { Dispatch, ReactNode, SetStateAction } from "react";

import { AttachmentPicker } from "@/components/AttachmentPicker";
import { ModelPicker } from "@/components/ModelPicker";
import type { ModelPickerProps } from "@/components/ModelPicker";
import type { SubtaskContextBrief } from "@/lib/types";

export type ChatComposerToolbarProps = {
  agentControl: ReactNode;
  attachmentsDisabled: boolean;
  canSend: boolean;
  cancelDisabled: boolean;
  cancelLabel: string;
  cancelling: boolean;
  contextLoading: boolean;
  draftContexts: SubtaskContextBrief[];
  label: string;
  modelPicker: ModelPickerProps;
  onCancel: () => void;
  onDraftContextsChange: Dispatch<SetStateAction<SubtaskContextBrief[]>>;
  onPendingChange: (pending: boolean) => void;
  onRetryContexts: () => void;
  recoveryError: string | null;
  sendLabel: string;
  sending: boolean;
  showCancel: boolean;
};

export function ChatComposerToolbar({
  agentControl,
  attachmentsDisabled,
  canSend,
  cancelDisabled,
  cancelLabel,
  cancelling,
  contextLoading,
  draftContexts,
  label,
  modelPicker,
  onCancel,
  onDraftContextsChange,
  onPendingChange,
  onRetryContexts,
  recoveryError,
  sendLabel,
  sending,
  showCancel,
}: ChatComposerToolbarProps) {
  return (
    <div
      aria-label={label}
      className="composer-toolbar composer-toolbar--wrap-safe"
      role="toolbar"
    >
      <div className="composer-toolbar__left" data-composer-group="left">
        <AttachmentPicker
          disabled={attachmentsDisabled}
          loading={contextLoading}
          onChange={onDraftContextsChange}
          onPendingChange={onPendingChange}
          onRetry={onRetryContexts}
          recoveryError={recoveryError}
          value={draftContexts}
        >
          {agentControl}
        </AttachmentPicker>
      </div>
      <div className="composer-toolbar__right" data-composer-group="right">
        <ModelPicker {...modelPicker} />
        {showCancel ? (
          <button
            aria-label={cancelLabel}
            className="send-button"
            disabled={cancelDisabled}
            onClick={onCancel}
            type="button"
          >
            {cancelling ? (
              <Loader2 aria-hidden="true" className="attachment-spinner" size={18} />
            ) : (
              <Square aria-hidden="true" size={16} />
            )}
          </button>
        ) : (
          <button
            aria-label={sendLabel}
            className="send-button"
            disabled={!canSend}
            type="submit"
          >
            {sending ? (
              <Loader2 aria-hidden="true" className="attachment-spinner" size={18} />
            ) : (
              <Send aria-hidden="true" size={18} />
            )}
          </button>
        )}
      </div>
    </div>
  );
}
