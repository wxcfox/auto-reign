"use client";

import { ChevronDown } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Agent } from "@/lib/types";

type AgentPickerProps = {
  agents: Agent[];
  disabled: boolean;
  onSelect: (agent: Agent) => void;
  selectedAgentId: string | null;
};

export function AgentPicker({
  agents,
  disabled,
  onSelect,
  selectedAgentId,
}: AgentPickerProps) {
  const { t } = useTranslation("chat");
  const listboxId = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId) ?? null;
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filteredAgents = agents.filter((agent) =>
    agent.name.toLocaleLowerCase().includes(normalizedQuery),
  );
  const globalAgents = filteredAgents.filter((agent) => agent.scope === "global");
  const privateAgents = filteredAgents.filter((agent) => agent.scope === "private");

  const chooseLabel = t("agentPicker.choose", { defaultValue: "Choose agent" });
  const listboxLabel = t("agentPicker.listbox", { defaultValue: "Agents" });

  const selectAgent = (agent: Agent) => {
    if (disabled) {
      return;
    }
    onSelect(agent);
    setOpen(false);
    setQuery("");
  };

  return (
    <div className="model-picker agent-picker" data-open={open}>
      <button
        aria-controls={open ? listboxId : undefined}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="model-picker-button agent-picker-button"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        type="button"
      >
        <span>{selectedAgent?.name ?? chooseLabel}</span>
        <ChevronDown aria-hidden="true" size={14} />
      </button>

      {open ? (
        <div
          className="model-picker-menu agent-picker-menu"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setOpen(false);
            }
          }}
          style={{ left: 0, right: "auto" }}
        >
          <input
            aria-label={t("agentPicker.search", { defaultValue: "Search agents" })}
            autoFocus
            disabled={disabled}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("agentPicker.search", { defaultValue: "Search agents" })}
            type="search"
            value={query}
          />
          <div aria-label={listboxLabel} id={listboxId} role="listbox">
            <AgentGroup
              agents={globalAgents}
              disabled={disabled}
              label={t("agentPicker.global", { defaultValue: "System agents" })}
              onSelect={selectAgent}
              selectedAgentId={selectedAgentId}
            />
            <AgentGroup
              agents={privateAgents}
              disabled={disabled}
              label={t("agentPicker.private", { defaultValue: "My agents" })}
              onSelect={selectAgent}
              selectedAgentId={selectedAgentId}
            />
          </div>
          <div className="agent-picker-footer">
            <Link href="/agents?create=1">{t("agentPicker.create")}</Link>
            <Link href="/agents">{t("agentPicker.manage")}</Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function AgentGroup({
  agents,
  disabled,
  label,
  onSelect,
  selectedAgentId,
}: {
  agents: Agent[];
  disabled: boolean;
  label: string;
  onSelect: (agent: Agent) => void;
  selectedAgentId: string | null;
}) {
  if (agents.length === 0) {
    return null;
  }

  return (
    <div aria-label={label} className="model-picker-group agent-picker-group" role="group">
      <p>{label}</p>
      {agents.map((agent) => {
        const active = agent.id === selectedAgentId;
        return (
          <button
            aria-selected={active}
            data-active={active}
            disabled={disabled}
            key={agent.id}
            onClick={() => onSelect(agent)}
            role="option"
            type="button"
          >
            {agent.name}
          </button>
        );
      })}
    </div>
  );
}
