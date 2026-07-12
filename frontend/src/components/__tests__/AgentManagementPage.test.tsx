import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentManagementPage } from "../AgentManagementPage";
import i18next from "@/i18n/setup";
import {
  createAgent,
  createGlobalAgent,
  createWorkspace,
  deleteAgent,
  deleteWorkspace,
  getModels,
  listAgents,
  listKnowledgeCollections,
  listKnowledgeDocuments,
  listWorkspaces,
  updateAgent,
} from "@/lib/api";
import type {
  Agent,
  KnowledgeCollection,
  ModelListResponse,
  Workspace,
} from "@/lib/types";

const navigationMocks = vi.hoisted(() => ({
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMocks,
}));

vi.mock("@/lib/api", () => ({
  createAgent: vi.fn(),
  createGlobalAgent: vi.fn(),
  createWorkspace: vi.fn(),
  deleteAgent: vi.fn(),
  deleteWorkspace: vi.fn(),
  getModels: vi.fn(),
  listAgents: vi.fn(),
  listKnowledgeCollections: vi.fn(),
  listKnowledgeDocuments: vi.fn(),
  listWorkspaces: vi.fn(),
  updateAgent: vi.fn(),
}));

const modelResponse: ModelListResponse = {
  providers: [{ provider: "qwen", models: ["qwen3.7-plus"] }],
  default: { provider: "qwen", model: "qwen3.7-plus" },
};

const privateAgent: Agent = {
  id: "agent-private",
  name: "Private helper",
  scope: "private",
  can_manage: true,
  is_active: true,
  config: {
    system_prompt: "Help privately.",
    default_model: null,
    home_workspace_id: null,
    knowledge_scopes: [],
  },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const inactiveAgent: Agent = {
  ...privateAgent,
  id: "agent-inactive",
  name: "Paused helper",
  is_active: false,
};

const globalAgent: Agent = {
  ...privateAgent,
  id: "agent-global",
  name: "Global helper",
  scope: "global",
};

const activeWorkspace: Workspace = {
  id: "workspace-active",
  name: "Active Home",
  scope: "private",
  can_manage: true,
  is_active: true,
  config: { workspace_type: "agent_home", initial_agents_md: "# Active" },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const inactiveWorkspace: Workspace = {
  ...activeWorkspace,
  id: "workspace-inactive",
  name: "Inactive Home",
  is_active: false,
};

const activeCollection: KnowledgeCollection = {
  id: "collection-active",
  name: "Active handbook",
  scope: "private",
  can_manage: true,
  is_active: true,
  config: {
    chunk_size: 900,
    chunk_overlap: 120,
    top_k: 8,
    score_threshold: null,
  },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const inactiveCollection: KnowledgeCollection = {
  ...activeCollection,
  id: "collection-inactive",
  name: "Inactive handbook",
  is_active: false,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function fillMinimalAgent(name = "New helper") {
  fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: name } });
  fireEvent.change(screen.getByLabelText("System prompt"), {
    target: { value: "Answer clearly." },
  });
}

async function openCreateAgent() {
  fireEvent.click(await screen.findByRole("button", { name: "Create Agent" }));
  return screen.getByRole("dialog", { name: "Create Agent" });
}

describe("AgentManagementPage", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    navigationMocks.replace.mockReset();
    await i18next.changeLanguage("en");
    vi.mocked(listAgents).mockResolvedValue({ agents: [privateAgent, inactiveAgent] });
    vi.mocked(listWorkspaces).mockResolvedValue({
      workspaces: [activeWorkspace, inactiveWorkspace],
    });
    vi.mocked(listKnowledgeCollections).mockResolvedValue({
      collections: [activeCollection, inactiveCollection],
    });
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({ documents: [] });
    vi.mocked(getModels).mockResolvedValue(modelResponse);
    vi.mocked(createAgent).mockResolvedValue(privateAgent);
    vi.mocked(createGlobalAgent).mockResolvedValue(globalAgent);
    vi.mocked(createWorkspace).mockResolvedValue(activeWorkspace);
    vi.mocked(updateAgent).mockResolvedValue(privateAgent);
    vi.mocked(deleteAgent).mockResolvedValue({ id: privateAgent.id, status: "deleted" });
    vi.mocked(deleteWorkspace).mockResolvedValue({
      id: activeWorkspace.id,
      status: "deleted",
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("loads inactive owned Agents with visible active resource options", async () => {
    const { container } = render(<AgentManagementPage scope="private" />);

    expect(await screen.findByText(privateAgent.name)).toBeInTheDocument();
    expect(screen.getByText(inactiveAgent.name)).toBeInTheDocument();
    expect(listAgents).toHaveBeenCalledWith("owned", { includeInactive: true });
    expect(listWorkspaces).toHaveBeenCalledWith("visible");
    expect(listKnowledgeCollections).toHaveBeenCalledWith("visible");
    expect(container.querySelector("main")).toBeNull();

    const dialog = await openCreateAgent();
    fireEvent.click(within(dialog).getByLabelText("Use an existing workspace"));
    expect(within(dialog).getByRole("option", { name: activeWorkspace.name })).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: inactiveWorkspace.name })).toBeNull();
    expect(within(dialog).getByRole("option", { name: activeCollection.name })).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: inactiveCollection.name })).toBeNull();
  });

  it("opens the initial private create form after loading and clears the query on close", async () => {
    const agentLoad = deferred<{ agents: Agent[] }>();
    vi.mocked(listAgents).mockReturnValue(agentLoad.promise);
    const view = render(
      <AgentManagementPage initialCreate scope="private" />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Loading Agents…");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    agentLoad.resolve({ agents: [privateAgent] });

    const dialog = await screen.findByRole("dialog", { name: "Create Agent" });
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Agent name")).toHaveFocus(),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(navigationMocks.replace).toHaveBeenCalledWith("/agents");
    view.rerender(<AgentManagementPage initialCreate scope="private" />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("clears the private create query only after a successful initial creation", async () => {
    render(<AgentManagementPage initialCreate scope="private" />);
    const dialog = await screen.findByRole("dialog", { name: "Create Agent" });
    fillMinimalAgent("Query helper");

    fireEvent.click(within(dialog).getByRole("button", { name: "Save agent" }));

    await waitFor(() => expect(createAgent).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(navigationMocks.replace).toHaveBeenCalledWith("/agents");
  });

  it("uses global resource scopes and the admin create endpoint", async () => {
    vi.mocked(listAgents).mockResolvedValue({ agents: [] });
    vi.mocked(listWorkspaces).mockResolvedValue({
      workspaces: [{ ...activeWorkspace, scope: "global" }],
    });
    vi.mocked(listKnowledgeCollections).mockResolvedValue({
      collections: [{ ...activeCollection, scope: "global" }],
    });
    render(<AgentManagementPage scope="global" />);

    fireEvent.click(await screen.findByRole("button", { name: "Create global Agent" }));
    fillMinimalAgent("Shared helper");
    fireEvent.click(screen.getByRole("button", { name: "Save agent" }));

    await waitFor(() => expect(createGlobalAgent).toHaveBeenCalledTimes(1));
    expect(createGlobalAgent).toHaveBeenCalledWith({
      name: "Shared helper",
      config: {
        system_prompt: "Answer clearly.",
        default_model: null,
        home_workspace_id: null,
        knowledge_scopes: [],
      },
    });
    expect(createAgent).not.toHaveBeenCalled();
    expect(listAgents).toHaveBeenCalledWith("global", { includeInactive: true });
    expect(listWorkspaces).toHaveBeenCalledWith("global");
    expect(listKnowledgeCollections).toHaveBeenCalledWith("global");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("keeps the dialog, draft, and local alert after a recoverable save failure", async () => {
    vi.mocked(createAgent)
      .mockRejectedValueOnce(new Error("database internals"))
      .mockResolvedValueOnce(privateAgent);
    render(<AgentManagementPage scope="private" />);
    const dialog = await openCreateAgent();
    fillMinimalAgent("Draft helper");

    fireEvent.click(within(dialog).getByRole("button", { name: "Save agent" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The Agent could not be saved.",
    );
    expect(within(dialog).getByLabelText("Agent name")).toHaveValue("Draft helper");
    expect(within(dialog).getByLabelText("System prompt")).toHaveValue("Answer clearly.");
    expect(screen.queryByText(/database internals/i)).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Save agent" }));
    await waitFor(() => expect(createAgent).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("keeps a successfully created Home when Agent creation fails", async () => {
    const createdHome = { ...activeWorkspace, id: "created-home", name: "My memory" };
    vi.mocked(createWorkspace).mockResolvedValue(createdHome);
    vi.mocked(createAgent).mockRejectedValue(new Error("duplicate Agent"));
    render(<AgentManagementPage scope="private" />);
    const dialog = await openCreateAgent();
    fillMinimalAgent("Home helper");
    fireEvent.click(within(dialog).getByLabelText("Create a new workspace"));
    fireEvent.change(within(dialog).getByLabelText("Workspace name"), {
      target: { value: createdHome.name },
    });
    fireEvent.change(within(dialog).getByLabelText("Initial AGENTS.md"), {
      target: { value: "# Memory rules" },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "Save agent" }));

    await waitFor(() => expect(createWorkspace).toHaveBeenCalledTimes(1));
    expect(createWorkspace).toHaveBeenCalledWith("private", {
      name: createdHome.name,
      config: { workspace_type: "agent_home", initial_agents_md: "# Memory rules" },
    });
    expect(createAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        config: expect.objectContaining({ home_workspace_id: createdHome.id }),
      }),
    );
    expect(deleteWorkspace).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Workspace My memory was created, but the Agent was not saved.",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(listWorkspaces).toHaveBeenCalledTimes(2);
  });

  it("enables an inactive Agent with a guarded whole-resource update", async () => {
    const pending = deferred<Agent>();
    vi.mocked(updateAgent).mockReturnValue(pending.promise);
    render(<AgentManagementPage scope="private" />);
    const enable = await screen.findByRole("button", {
      name: `Enable ${inactiveAgent.name}`,
    });

    fireEvent.click(enable);
    fireEvent.click(enable);

    expect(updateAgent).toHaveBeenCalledTimes(1);
    expect(updateAgent).toHaveBeenCalledWith(inactiveAgent.id, {
      name: inactiveAgent.name,
      config: inactiveAgent.config,
      is_active: true,
    });
    expect(enable).toBeDisabled();
    pending.resolve({ ...inactiveAgent, is_active: true });
    await waitFor(() => expect(listAgents).toHaveBeenCalledTimes(2));
  });

  it("shows a stable status error, unlocks, and guards each retry", async () => {
    vi.mocked(updateAgent)
      .mockRejectedValueOnce(new Error("database and provider internals"))
      .mockResolvedValueOnce({ ...inactiveAgent, is_active: true });
    render(<AgentManagementPage scope="private" />);
    const enable = await screen.findByRole("button", {
      name: `Enable ${inactiveAgent.name}`,
    });

    fireEvent.click(enable);
    fireEvent.click(enable);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The Agent status could not be changed.",
    );
    expect(screen.queryByText(/database and provider internals/i)).not.toBeInTheDocument();
    expect(updateAgent).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(enable).toBeEnabled());

    fireEvent.click(enable);
    fireEvent.click(enable);

    await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(2));
    expect(updateAgent).toHaveBeenLastCalledWith(inactiveAgent.id, {
      name: inactiveAgent.name,
      config: inactiveAgent.config,
      is_active: true,
    });
    await waitFor(() => expect(listAgents).toHaveBeenCalledTimes(2));
    expect(
      screen.queryByText("The Agent status could not be changed."),
    ).not.toBeInTheDocument();
  });

  it("preserves inactive state when editing an inactive Agent", async () => {
    render(<AgentManagementPage scope="private" />);
    fireEvent.click(
      await screen.findByRole("button", { name: `Edit ${inactiveAgent.name}` }),
    );
    const dialog = screen.getByRole("dialog", { name: `Edit ${inactiveAgent.name}` });
    fireEvent.change(within(dialog).getByLabelText("System prompt"), {
      target: { value: "Updated while paused." },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save agent" }));

    await waitFor(() =>
      expect(updateAgent).toHaveBeenCalledWith(inactiveAgent.id, {
        name: inactiveAgent.name,
        config: {
          ...inactiveAgent.config,
          system_prompt: "Updated while paused.",
        },
        is_active: false,
      }),
    );
  });

  it("uses named guarded delete controls", async () => {
    const pending = deferred<{ id: string; status: "deleted" }>();
    vi.mocked(deleteAgent).mockReturnValue(pending.promise);
    render(<AgentManagementPage scope="private" />);
    const remove = await screen.findByRole("button", {
      name: `Delete ${privateAgent.name}`,
    });

    fireEvent.click(remove);
    fireEvent.click(remove);

    expect(window.confirm).toHaveBeenCalledWith(`Delete ${privateAgent.name}?`);
    expect(deleteAgent).toHaveBeenCalledTimes(1);
    expect(remove).toBeDisabled();
    pending.resolve({ id: privateAgent.id, status: "deleted" });
    await waitFor(() => expect(listAgents).toHaveBeenCalledTimes(2));
  });

  it("shows a stable delete error, unlocks, and guards each retry", async () => {
    vi.mocked(deleteAgent)
      .mockRejectedValueOnce(new Error("storage and token internals"))
      .mockResolvedValueOnce({ id: privateAgent.id, status: "deleted" });
    render(<AgentManagementPage scope="private" />);
    const remove = await screen.findByRole("button", {
      name: `Delete ${privateAgent.name}`,
    });

    fireEvent.click(remove);
    fireEvent.click(remove);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The Agent could not be deleted.",
    );
    expect(screen.queryByText(/storage and token internals/i)).not.toBeInTheDocument();
    expect(deleteAgent).toHaveBeenCalledTimes(1);
    expect(window.confirm).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(remove).toBeEnabled());

    fireEvent.click(remove);
    fireEvent.click(remove);

    await waitFor(() => expect(deleteAgent).toHaveBeenCalledTimes(2));
    expect(window.confirm).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(listAgents).toHaveBeenCalledTimes(2));
    expect(
      screen.queryByText("The Agent could not be deleted."),
    ).not.toBeInTheDocument();
  });

  it("disables every mutation control when the Agent cannot be managed", async () => {
    const readOnlyAgent: Agent = {
      ...privateAgent,
      id: "agent-read-only",
      name: "Read-only helper",
      can_manage: false,
    };
    vi.mocked(listAgents).mockResolvedValue({ agents: [readOnlyAgent] });
    render(<AgentManagementPage scope="private" />);

    const edit = await screen.findByRole("button", {
      name: `Edit ${readOnlyAgent.name}`,
    });
    const disable = screen.getByRole("button", {
      name: `Disable ${readOnlyAgent.name}`,
    });
    const remove = screen.getByRole("button", {
      name: `Delete ${readOnlyAgent.name}`,
    });
    expect(edit).toBeDisabled();
    expect(disable).toBeDisabled();
    expect(remove).toBeDisabled();

    fireEvent.click(edit);
    fireEvent.click(disable);
    fireEvent.click(remove);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(updateAgent).not.toHaveBeenCalled();
    expect(deleteAgent).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it("provides a labelled modal, focus trap, Escape/backdrop close, and focus restore", async () => {
    render(<AgentManagementPage scope="private" />);
    const trigger = await screen.findByRole("button", { name: "Create Agent" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Create Agent" });
    const name = within(dialog).getByLabelText("Agent name");
    const save = within(dialog).getByRole("button", { name: "Save agent" });
    await waitFor(() => expect(name).toHaveFocus());

    save.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(name).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(save).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());

    fireEvent.click(trigger);
    const reopened = screen.getByRole("dialog", { name: "Create Agent" });
    const backdrop = reopened.closest(".dialog-backdrop");
    expect(backdrop).not.toBeNull();
    fireEvent.mouseDown(backdrop!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("cannot close the dialog while a save is pending", async () => {
    const pending = deferred<Agent>();
    vi.mocked(createAgent).mockReturnValue(pending.promise);
    render(<AgentManagementPage scope="private" />);
    const dialog = await openCreateAgent();
    fillMinimalAgent();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save agent" }));
    await waitFor(() => expect(createAgent).toHaveBeenCalledTimes(1));

    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.mouseDown(dialog.closest(".dialog-backdrop")!);

    expect(screen.getByRole("dialog", { name: "Create Agent" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    pending.resolve(privateAgent);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows invalid legacy references explicitly and lets the user remove them", async () => {
    const invalidAgent: Agent = {
      ...privateAgent,
      id: "agent-invalid",
      name: "Legacy helper",
      config: {
        ...privateAgent.config,
        home_workspace_id: "missing-workspace",
        knowledge_scopes: [
          { collection_id: "missing-collection", document_ids: null },
        ],
      },
    };
    vi.mocked(listAgents).mockResolvedValue({ agents: [invalidAgent] });
    vi.mocked(listWorkspaces).mockResolvedValue({ workspaces: [] });
    vi.mocked(listKnowledgeCollections).mockResolvedValue({ collections: [] });
    render(<AgentManagementPage scope="private" />);
    fireEvent.click(
      await screen.findByRole("button", { name: `Edit ${invalidAgent.name}` }),
    );
    const dialog = screen.getByRole("dialog", { name: `Edit ${invalidAgent.name}` });

    expect(within(dialog).getByRole("alert", { name: "Workspace unavailable" })).toHaveTextContent(
      "missing-workspace",
    );
    expect(
      within(dialog).getByRole("alert", { name: "Collection unavailable" }),
    ).toHaveTextContent("missing-collection");
    const save = within(dialog).getByRole("button", { name: "Save agent" });
    expect(save).toBeDisabled();
    fireEvent.click(within(dialog).getByLabelText("Do not use a workspace"));
    expect(save).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(within(dialog).queryByText("missing-workspace")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("missing-collection")).not.toBeInTheDocument();
    expect(save).toBeEnabled();
  });

  it("ignores a stale load after the management scope changes", async () => {
    const privateLoad = deferred<{ agents: Agent[] }>();
    const globalLoad = deferred<{ agents: Agent[] }>();
    vi.mocked(listAgents).mockImplementation((scope) =>
      scope === "owned" ? privateLoad.promise : globalLoad.promise,
    );
    const view = render(<AgentManagementPage scope="private" />);
    await waitFor(() =>
      expect(listAgents).toHaveBeenCalledWith("owned", { includeInactive: true }),
    );
    view.rerender(<AgentManagementPage scope="global" />);
    await waitFor(() =>
      expect(listAgents).toHaveBeenCalledWith("global", { includeInactive: true }),
    );

    globalLoad.resolve({ agents: [globalAgent] });
    expect(await screen.findByText(globalAgent.name)).toBeInTheDocument();
    privateLoad.resolve({ agents: [privateAgent] });
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByText(privateAgent.name)).not.toBeInTheDocument();
    expect(screen.getByText(globalAgent.name)).toBeInTheDocument();
  });

  it("ignores stale mutation completion and unlocks the new management scope", async () => {
    const pending = deferred<Agent>();
    vi.mocked(updateAgent).mockReturnValue(pending.promise);
    vi.mocked(listAgents).mockImplementation((requestedScope) =>
      Promise.resolve({
        agents: requestedScope === "global" ? [globalAgent] : [inactiveAgent],
      }),
    );
    const view = render(<AgentManagementPage scope="private" />);
    fireEvent.click(
      await screen.findByRole("button", { name: `Enable ${inactiveAgent.name}` }),
    );
    expect(updateAgent).toHaveBeenCalledTimes(1);

    view.rerender(<AgentManagementPage scope="global" />);
    expect(await screen.findByText(globalAgent.name)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create global Agent" }),
    ).toBeEnabled();

    await act(async () => {
      pending.resolve({ ...inactiveAgent, is_active: true });
      await pending.promise;
    });

    expect(listAgents).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(inactiveAgent.name)).not.toBeInTheDocument();
    expect(screen.getByText(globalAgent.name)).toBeInTheDocument();
  });

  it("renders a recoverable stable load error", async () => {
    vi.mocked(listAgents)
      .mockRejectedValueOnce(new Error("database details"))
      .mockResolvedValueOnce({ agents: [privateAgent] });
    render(<AgentManagementPage scope="private" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Agents could not be loaded.",
    );
    expect(screen.queryByText(/database details/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(privateAgent.name)).toBeInTheDocument();
  });
});
