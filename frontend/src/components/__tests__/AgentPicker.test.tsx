import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentPicker } from "../AgentPicker";
import i18next from "@/i18n/setup";
import type { Agent } from "@/lib/types";

const globalAgent: Agent = {
  id: "agent-global",
  name: "Interview coach",
  scope: "global",
  can_manage: false,
  is_active: true,
  config: {
    system_prompt: "Coach the user.",
    default_model: { provider: "qwen", model: "qwen3.7-plus" },
    home_workspace_id: null,
    knowledge_scopes: [],
  },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const privateAgent: Agent = {
  ...globalAgent,
  id: "agent-private",
  name: "Private research agent",
  scope: "private",
  can_manage: true,
};

describe("AgentPicker", () => {
  beforeEach(async () => {
    await i18next.changeLanguage("en");
  });

  it("groups global and private agents and filters by name", () => {
    render(
      <AgentPicker
        agents={[globalAgent, privateAgent]}
        disabled={false}
        onSelect={vi.fn()}
        selectedAgentId={globalAgent.id}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: globalAgent.name }));

    expect(screen.getByRole("listbox", { name: /agents/i })).toBeInTheDocument();
    expect(screen.getByText("System agents")).toBeInTheDocument();
    expect(screen.getByText("My agents")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: globalAgent.name })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "PRIVATE" } });

    expect(screen.queryByRole("option", { name: globalAgent.name })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: privateAgent.name })).toBeInTheDocument();
  });

  it("offers real private Agent create and manage links, then selects an Agent", () => {
    const onSelect = vi.fn();
    render(
      <AgentPicker
        agents={[globalAgent, privateAgent]}
        disabled={false}
        onSelect={onSelect}
        selectedAgentId={null}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /choose agent/i }));
    expect(screen.getByRole("link", { name: /create agent/i })).toHaveAttribute(
      "href",
      "/agents?create=1",
    );
    expect(screen.getByRole("link", { name: /manage agents/i })).toHaveAttribute(
      "href",
      "/agents",
    );
    expect(
      screen.queryByRole("link", { name: /global agent management/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: privateAgent.name }));

    expect(onSelect).toHaveBeenCalledWith(privateAgent);
    expect(screen.queryByRole("listbox", { name: /agents/i })).not.toBeInTheDocument();
  });

  it("offers an explicit no-Agent option", () => {
    const onClear = vi.fn();
    render(
      <AgentPicker
        agents={[globalAgent]}
        disabled={false}
        onClear={onClear}
        onSelect={vi.fn()}
        selectedAgentId={globalAgent.id}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: globalAgent.name }));
    fireEvent.click(screen.getByRole("option", { name: /no agent/i }));

    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("disables search and selection together", () => {
    const onSelect = vi.fn();
    render(
      <AgentPicker
        agents={[globalAgent]}
        disabled
        onSelect={onSelect}
        selectedAgentId={globalAgent.id}
      />,
    );

    const trigger = screen.getByRole("button", { name: globalAgent.name });
    expect(trigger).toBeDisabled();
    fireEvent.click(trigger);
    expect(screen.queryByRole("listbox", { name: /agents/i })).not.toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("localizes the private management links", async () => {
    await i18next.changeLanguage("zh-CN");
    render(
      <AgentPicker
        agents={[privateAgent]}
        disabled={false}
        onSelect={vi.fn()}
        selectedAgentId={null}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "选择智能体" }));
    expect(screen.getByRole("link", { name: "创建智能体" })).toHaveAttribute(
      "href",
      "/agents?create=1",
    );
    expect(screen.getByRole("link", { name: "管理智能体" })).toHaveAttribute(
      "href",
      "/agents",
    );
  });
});
