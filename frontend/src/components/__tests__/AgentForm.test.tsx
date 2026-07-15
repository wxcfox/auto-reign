import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentForm } from "../AgentForm";
import i18next, { namespaces } from "@/i18n/setup";
import { listKnowledgeDocuments } from "@/lib/api";
import type {
  Agent,
  KnowledgeCollection,
  KnowledgeDocument,
  ModelListResponse,
  Workspace,
} from "@/lib/types";

vi.mock("@/lib/api", () => ({
  listKnowledgeDocuments: vi.fn(),
}));

const modelResponse: ModelListResponse = {
  providers: [
    { provider: "qwen", models: ["qwen3.7-plus", "qwen3.7-flash"] },
    { provider: "openai-compatible", models: ["chat-model"] },
  ],
  default: { provider: "qwen", model: "qwen3.7-flash" },
};

const workspaceFixture: Workspace = {
  id: "workspace-1",
  name: "Existing memory",
  scope: "private",
  can_manage: true,
  is_active: true,
  config: { workspace_type: "agent_home", initial_agents_md: "# Existing" },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const collectionOne: KnowledgeCollection = {
  id: "collection-1",
  name: "Engineering handbook",
  scope: "global",
  can_manage: false,
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

const collectionTwo: KnowledgeCollection = {
  ...collectionOne,
  id: "collection-2",
  name: "Learning notes",
};

const documentFixture: KnowledgeDocument = {
  id: "doc-2",
  collection_id: collectionTwo.id,
  name: "Selected.md",
  mime_type: "text/markdown",
  size_bytes: 100,
  status: "ready",
  index_generation: 1,
  error_code: null,
  error_message: null,
  is_active: true,
  indexed_at: "2026-07-13T00:01:00Z",
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:01:00Z",
};

const editAgent: Agent = {
  id: "agent-1",
  name: "Existing agent",
  scope: "private",
  can_manage: true,
  is_active: true,
  config: {
    system_prompt: "Existing prompt",
    default_model: { provider: "qwen", model: "qwen3.7-flash" },
    home_workspace_id: workspaceFixture.id,
    knowledge_scopes: [
      { collection_id: collectionTwo.id, document_ids: [documentFixture.id] },
    ],
  },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

function renderForm(
  overrides: Partial<React.ComponentProps<typeof AgentForm>> = {},
) {
  const props: React.ComponentProps<typeof AgentForm> = {
    agent: null,
    collections: [collectionOne, collectionTwo],
    models: modelResponse,
    onCancel: vi.fn(),
    onSubmit: vi.fn().mockResolvedValue(undefined),
    saving: false,
    workspaces: [workspaceFixture],
    ...overrides,
  };
  return { ...render(<AgentForm {...props} />), props };
}

describe("AgentForm", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18next.changeLanguage("en");
    vi.mocked(listKnowledgeDocuments).mockResolvedValue({
      documents: [documentFixture],
    });
  });

  it("submits prompt, custom model, a new home, and two knowledge scopes", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderForm({ onSubmit });

    fireEvent.change(screen.getByLabelText("Agent name"), {
      target: { value: "Growth helper" },
    });
    fireEvent.change(screen.getByLabelText("System prompt"), {
      target: { value: "Preserve evidence and answer clearly." },
    });
    fireEvent.click(screen.getByLabelText("Use a specific model"));
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "qwen" } });
    fireEvent.change(screen.getByLabelText("Model"), {
      target: { value: "qwen3.7-plus" },
    });
    fireEvent.click(screen.getByLabelText("Create a new workspace"));
    fireEvent.change(screen.getByLabelText("Workspace name"), {
      target: { value: "My memory" },
    });
    fireEvent.change(screen.getByLabelText("Initial AGENTS.md"), {
      target: { value: "# Memory rules\nKeep sources." },
    });

    const addCollection = screen.getByLabelText("Add collection");
    fireEvent.change(addCollection, { target: { value: collectionOne.id } });
    fireEvent.click(screen.getByRole("button", { name: "Add knowledge scope" }));
    fireEvent.change(addCollection, { target: { value: collectionTwo.id } });
    fireEvent.click(screen.getByRole("button", { name: "Add knowledge scope" }));
    fireEvent.click(screen.getAllByLabelText("Selected documents")[1]);
    fireEvent.click(await screen.findByLabelText("Selected.md"));
    const save = screen.getByRole("button", { name: "Save agent" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith({
      agent: {
        name: "Growth helper",
        config: {
          system_prompt: "Preserve evidence and answer clearly.",
          default_model: { provider: "qwen", model: "qwen3.7-plus" },
          home_workspace_id: null,
          knowledge_scopes: [
            { collection_id: collectionOne.id, document_ids: null },
            { collection_id: collectionTwo.id, document_ids: [documentFixture.id] },
          ],
        },
      },
      workspace: {
        name: "My memory",
        config: {
          workspace_type: "agent_home",
          initial_agents_md: "# Memory rules\nKeep sources.",
        },
      },
    });
  });

  it("shows a localized validation error instead of sending an empty subset", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderForm({ onSubmit });

    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "Agent" } });
    fireEvent.change(screen.getByLabelText("System prompt"), { target: { value: "Help." } });
    fireEvent.change(screen.getByLabelText("Add collection"), {
      target: { value: collectionOne.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add knowledge scope" }));
    fireEvent.click(screen.getByLabelText("Selected documents"));
    expect(await screen.findByLabelText("Selected.md")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save agent" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Select at least one document.",
    );
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("maps edit values and reliably resets when the agent prop changes", async () => {
    const { rerender } = renderForm({ agent: editAgent });

    expect(screen.getByLabelText("Agent name")).toHaveValue(editAgent.name);
    expect(screen.getByLabelText("System prompt")).toHaveValue("Existing prompt");
    expect(screen.getByLabelText("Use a specific model")).toBeChecked();
    expect(screen.getByLabelText("Provider")).toHaveValue("qwen");
    expect(screen.getByLabelText("Model")).toHaveValue("qwen3.7-flash");
    expect(screen.getByLabelText("Use an existing workspace")).toBeChecked();
    expect(screen.getByLabelText("Workspace")).toHaveValue(workspaceFixture.id);
    expect(await screen.findByLabelText("Selected.md")).toBeChecked();

    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "Unsaved" } });
    const replacement: Agent = {
      ...editAgent,
      id: "agent-2",
      name: "Replacement agent",
      updated_at: "2026-07-13T01:00:00Z",
      config: {
        system_prompt: "Replacement prompt",
        default_model: null,
        home_workspace_id: null,
        knowledge_scopes: [],
      },
    };
    rerender(
      <AgentForm
        agent={replacement}
        collections={[collectionOne, collectionTwo]}
        models={modelResponse}
        onCancel={vi.fn()}
        onSubmit={vi.fn().mockResolvedValue(undefined)}
        saving={false}
        workspaces={[workspaceFixture]}
      />,
    );

    await waitFor(() =>
      expect(screen.getByLabelText("Agent name")).toHaveValue("Replacement agent"),
    );
    expect(screen.getByLabelText("System prompt")).toHaveValue("Replacement prompt");
    expect(screen.getByLabelText("Follow system default model")).toBeChecked();
    expect(screen.getByLabelText("Do not use a workspace")).toBeChecked();
    expect(
      screen.queryByRole("region", { name: collectionTwo.name }),
    ).not.toBeInTheDocument();
  });

  it("shows a removed default model explicitly and lets the user replace it", async () => {
    const unavailableModelAgent: Agent = {
      ...editAgent,
      config: {
        ...editAgent.config,
        default_model: { provider: "retired-provider", model: "retired-model" },
        knowledge_scopes: [],
      },
    };
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderForm({ agent: unavailableModelAgent, onSubmit });

    expect(screen.getByLabelText("Provider")).toHaveValue("retired-provider");
    expect(screen.getByRole("option", { name: "Unavailable provider · retired-provider" }))
      .toBeInTheDocument();
    expect(screen.getByLabelText("Model")).toHaveValue("retired-model");
    expect(screen.getByRole("option", { name: "Unavailable model · retired-model" }))
      .toBeInTheDocument();
    expect(
      screen.getByRole("alert", { name: "Default model unavailable" }),
    ).toHaveTextContent("retired-provider / retired-model");
    const save = screen.getByRole("button", { name: "Save agent" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "qwen" } });
    fireEvent.change(screen.getByLabelText("Model"), {
      target: { value: "qwen3.7-plus" },
    });
    expect(
      screen.queryByRole("alert", { name: "Default model unavailable" }),
    ).not.toBeInTheDocument();
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        agent: expect.objectContaining({
          config: expect.objectContaining({
            default_model: { provider: "qwen", model: "qwen3.7-plus" },
          }),
        }),
      }),
    );
  });

  it("blocks stale Home and Collection references until both are removed", async () => {
    const unavailableReferencesAgent: Agent = {
      ...editAgent,
      config: {
        ...editAgent.config,
        default_model: null,
        home_workspace_id: "missing-workspace",
        knowledge_scopes: [
          { collection_id: "missing-collection", document_ids: null },
        ],
      },
    };
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = renderForm({
      agent: unavailableReferencesAgent,
      collections: [],
      onSubmit,
      workspaces: [],
    });
    const save = screen.getByRole("button", { name: "Save agent" });

    expect(save).toBeDisabled();
    fireEvent.submit(container.querySelector("form")!);
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("Do not use a workspace"));
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        agent: expect.objectContaining({
          config: expect.objectContaining({
            home_workspace_id: null,
            knowledge_scopes: [],
          }),
        }),
      }),
    );
  });

  it("blocks an unconfirmed or missing selected document until the scope is repaired", async () => {
    let resolveDocuments!: (value: { documents: KnowledgeDocument[] }) => void;
    vi.mocked(listKnowledgeDocuments).mockReturnValue(
      new Promise((resolve) => {
        resolveDocuments = resolve;
      }),
    );
    const missingDocumentId = "missing-selected-document";
    const selectedDocumentAgent: Agent = {
      ...editAgent,
      config: {
        ...editAgent.config,
        home_workspace_id: null,
        knowledge_scopes: [
          { collection_id: collectionOne.id, document_ids: [missingDocumentId] },
        ],
      },
    };
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = renderForm({ agent: selectedDocumentAgent, onSubmit });
    const save = screen.getByRole("button", { name: "Save agent" });

    expect(save).toBeDisabled();
    fireEvent.submit(container.querySelector("form")!);
    expect(onSubmit).not.toHaveBeenCalled();

    resolveDocuments({ documents: [documentFixture] });
    expect(
      await screen.findByRole("alert", {
        name: `Selected document unavailable · ${missingDocumentId}`,
      }),
    ).toHaveTextContent(missingDocumentId);
    expect(save).toBeDisabled();
    fireEvent.submit(container.querySelector("form")!);
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("Entire collection"));
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        agent: expect.objectContaining({
          config: expect.objectContaining({
            knowledge_scopes: [
              { collection_id: collectionOne.id, document_ids: null },
            ],
          }),
        }),
      }),
    );
  });

  it("blocks a failed document lookup until the subset is replaced", async () => {
    vi.mocked(listKnowledgeDocuments).mockRejectedValue(
      new Error("document service details"),
    );
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = renderForm({ agent: editAgent, onSubmit });
    const save = screen.getByRole("button", { name: "Save agent" });

    expect(save).toBeDisabled();
    expect(await screen.findByText("Documents could not be loaded.")).toBeInTheDocument();
    expect(screen.queryByText(/document service details/i)).not.toBeInTheDocument();
    fireEvent.submit(container.querySelector("form")!);
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("Entire collection"));
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.submit(container.querySelector("form")!);

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        agent: expect.objectContaining({
          config: expect.objectContaining({
            knowledge_scopes: [
              { collection_id: collectionTwo.id, document_ids: null },
            ],
          }),
        }),
      }),
    );
  });

  it("resets candidate, failure, and document cache when switching mounted agents", async () => {
    const oldDocument = { ...documentFixture, id: "old-doc", name: "Old.md" };
    const newDocument = { ...documentFixture, id: "new-doc", name: "New.md" };
    vi.mocked(listKnowledgeDocuments)
      .mockReset()
      .mockResolvedValueOnce({ documents: [oldDocument] })
      .mockRejectedValueOnce(new Error("second agent is temporarily unavailable"))
      .mockResolvedValueOnce({ documents: [newDocument] });
    const view = renderForm({ agent: editAgent });

    expect(await screen.findByLabelText("Old.md")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Add collection"), {
      target: { value: collectionOne.id },
    });
    expect(screen.getByLabelText("Add collection")).toHaveValue(collectionOne.id);

    const secondAgent: Agent = {
      ...editAgent,
      id: "agent-2",
      updated_at: "2026-07-13T01:00:00Z",
    };
    view.rerender(
      <AgentForm
        agent={secondAgent}
        collections={[collectionOne, collectionTwo]}
        models={modelResponse}
        onCancel={vi.fn()}
        onSubmit={vi.fn().mockResolvedValue(undefined)}
        saving={false}
        workspaces={[workspaceFixture]}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Documents could not be loaded.",
    );
    expect(screen.getByLabelText("Add collection")).toHaveValue("");
    expect(screen.queryByLabelText("Old.md")).not.toBeInTheDocument();

    const thirdAgent: Agent = {
      ...editAgent,
      id: "agent-3",
      updated_at: "2026-07-13T02:00:00Z",
      config: {
        ...editAgent.config,
        knowledge_scopes: [
          { collection_id: collectionTwo.id, document_ids: [newDocument.id] },
        ],
      },
    };
    view.rerender(
      <AgentForm
        agent={thirdAgent}
        collections={[collectionOne, collectionTwo]}
        models={modelResponse}
        onCancel={vi.fn()}
        onSubmit={vi.fn().mockResolvedValue(undefined)}
        saving={false}
        workspaces={[workspaceFixture]}
      />,
    );

    expect(await screen.findByLabelText("New.md")).toBeChecked();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(listKnowledgeDocuments).toHaveBeenCalledTimes(3);
  });

  it("shows an explicit empty state when existing Home has no available workspace", () => {
    renderForm({ workspaces: [] });

    fireEvent.click(screen.getByLabelText("Use an existing workspace"));

    expect(screen.getByText("No workspaces are available.")).toBeInTheDocument();
    expect(screen.getByLabelText("Workspace")).toBeDisabled();
  });

  it("does not submit programmatically while saving", () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { container } = renderForm({ agent: editAgent, onSubmit, saving: true });
    const form = container.querySelector("form");
    expect(form).not.toBeNull();

    fireEvent.submit(form!);
    fireEvent.submit(form!);

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  });

  it("deduplicates same-tick submits while the first submission is pending", async () => {
    let resolveSubmit!: () => void;
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveSubmit = resolve;
        }),
    );
    const agentWithoutKnowledge: Agent = {
      ...editAgent,
      config: { ...editAgent.config, knowledge_scopes: [] },
    };
    const { container } = renderForm({
      agent: agentWithoutKnowledge,
      onSubmit,
      saving: false,
    });
    const form = container.querySelector("form");
    expect(form).not.toBeNull();

    fireEvent.submit(form!);
    fireEvent.submit(form!);

    expect(onSubmit).toHaveBeenCalledTimes(1);
    resolveSubmit();
    await Promise.resolve();
    await Promise.resolve();
    fireEvent.submit(form!);
    expect(onSubmit).toHaveBeenCalledTimes(2);
    resolveSubmit();
  });

  it("uses complete Chinese resources without falling back to English", async () => {
    await i18next.changeLanguage("zh-CN");
    renderForm({ workspaces: [] });

    expect(namespaces).toContain("agents");
    expect(screen.getByLabelText("智能体名称")).toBeInTheDocument();
    expect(screen.getByLabelText("系统提示词")).toBeInTheDocument();
    expect(screen.getByLabelText("跟随系统默认模型")).toBeInTheDocument();
    expect(screen.getByLabelText("新建智能体工作区")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("添加资料库"), {
      target: { value: collectionOne.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加资料库范围" }));
    expect(screen.getByText("整库")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存智能体" })).toBeInTheDocument();
    expect(screen.queryByText("Save agent")).not.toBeInTheDocument();

    const requiredChineseKeys = [
      "fields.name",
      "fields.system_prompt",
      "model.follow_system",
      "home.create",
      "home.no_workspaces",
      "knowledge.entire_collection",
      "knowledge.selected_documents",
      "knowledge.status.processing",
      "actions.save",
      "errors.name_required",
      "errors.system_prompt_required",
      "errors.default_model_required",
      "errors.home_workspace_required",
      "errors.workspace_name_required",
      "errors.agents_md_required",
      "errors.knowledge_collection_required",
      "errors.knowledge_collection_duplicate",
      "errors.knowledge_subset_required",
    ];
    for (const key of requiredChineseKeys) {
      const value = i18next.getResource("zh-CN", "agents", key);
      expect(value, `missing zh-CN agents key: ${key}`).toBeTypeOf("string");
      expect(value).not.toBe("");
    }
    expect(leafKeys(i18next.getResourceBundle("zh-CN", "agents"))).toEqual(
      leafKeys(i18next.getResourceBundle("en", "agents")),
    );
  });
});

function leafKeys(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return [prefix];
  }
  return Object.entries(value)
    .flatMap(([key, child]) => leafKeys(child, prefix ? `${prefix}.${key}` : key))
    .sort();
}
