"use client";

export type ChatTopbarProps = {
  model: string;
  title: string;
};

export function ChatTopbar({ model, title }: ChatTopbarProps) {
  return (
    <header className="chat-topbar">
      <span className="chat-topbar-title">{title}</span>
      <span className="chat-topbar-model">{model}</span>
    </header>
  );
}
