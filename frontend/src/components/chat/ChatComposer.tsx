"use client";

import type { FormEvent } from "react";

import { AutoResizeTextarea } from "@/components/AutoResizeTextarea";

import { ChatComposerToolbar, type ChatComposerToolbarProps } from "./ChatComposerToolbar";

export type ChatComposerProps = {
  disconnectedNotice: string | null;
  inputDisabled: boolean;
  inputLabel: string;
  label: string;
  onChange: (value: string) => void;
  onSubmit: (event?: FormEvent<HTMLFormElement>) => void;
  placeholder: string;
  toolbar: ChatComposerToolbarProps;
  unavailableNotice: string | null;
  value: string;
};

export function ChatComposer({
  disconnectedNotice,
  inputDisabled,
  inputLabel,
  label,
  onChange,
  onSubmit,
  placeholder,
  toolbar,
  unavailableNotice,
  value,
}: ChatComposerProps) {
  return (
    <>
      {disconnectedNotice ? <p role="status">{disconnectedNotice}</p> : null}
      {unavailableNotice ? <p role="status">{unavailableNotice}</p> : null}
      <form aria-label={label} className="chat-composer" onSubmit={onSubmit}>
        <div className="composer-box">
          <label className="sr-only" htmlFor="chat-composer">{inputLabel}</label>
          <AutoResizeTextarea
            aria-label={inputLabel}
            disabled={inputDisabled}
            id="chat-composer"
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSubmit();
              }
            }}
            placeholder={placeholder}
            value={value}
          />
          <ChatComposerToolbar {...toolbar} />
        </div>
      </form>
    </>
  );
}
