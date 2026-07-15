import { describe, expect, it } from "vitest";

import {
  AgentFormValidationError,
  agentToFormState,
  buildAgentSubmission,
  emptyAgentFormState,
} from "../agent-form-state";
import type { Agent } from "@/lib/types";

describe("emptyAgentFormState", () => {
  it("starts in follow-default, no-home, and no-knowledge modes", () => {
    expect(emptyAgentFormState()).toEqual({
      name: "",
      systemPrompt: "",
      defaultModelMode: "follow",
      defaultProvider: "",
      defaultModel: "",
      homeMode: "none",
      workspaceId: "",
      newWorkspaceName: "",
      initialAgentsMd: "",
      knowledgeScopes: [],
    });
  });
});

describe("buildAgentSubmission", () => {
  it("normalizes a complete agent and a newly requested home", () => {
    const result = buildAgentSubmission({
      ...emptyAgentFormState(),
      name: "  Growth helper  ",
      systemPrompt: "  Help me learn from evidence.  ",
      defaultModelMode: "custom",
      defaultProvider: "  qwen  ",
      defaultModel: "  qwen3.7-plus  ",
      homeMode: "create",
      newWorkspaceName: "  Learning home  ",
      initialAgentsMd: "  # Rules\nPreserve source text.\n  ",
      knowledgeScopes: [
        {
          collectionId: "  collection-all  ",
          mode: "all",
          documentIds: ["ignored-document"],
        },
        {
          collectionId: "collection-subset",
          mode: "subset",
          documentIds: ["doc-2", "doc-1", "doc-2"],
        },
      ],
    });

    expect(result).toEqual({
      agent: {
        name: "Growth helper",
        config: {
          system_prompt: "Help me learn from evidence.",
          default_model: { provider: "qwen", model: "qwen3.7-plus" },
          home_workspace_id: null,
          knowledge_scopes: [
            { collection_id: "collection-all", document_ids: null },
            {
              collection_id: "collection-subset",
              document_ids: ["doc-2", "doc-1"],
            },
          ],
        },
      },
      workspace: {
        name: "Learning home",
        config: {
          workspace_type: "agent_home",
          initial_agents_md: "# Rules\nPreserve source text.",
        },
      },
    });
  });

  it("maps follow-default, no home, and no knowledge to null and empty config", () => {
    const result = buildAgentSubmission({
      ...emptyAgentFormState(),
      name: "Plain chat",
      systemPrompt: "Be concise.",
      defaultProvider: "ignored-provider",
      defaultModel: "ignored-model",
    });

    expect(result).toEqual({
      agent: {
        name: "Plain chat",
        config: {
          system_prompt: "Be concise.",
          default_model: null,
          home_workspace_id: null,
          knowledge_scopes: [],
        },
      },
      workspace: null,
    });
  });

  it("uses an existing home without creating a workspace", () => {
    const result = buildAgentSubmission({
      ...emptyAgentFormState(),
      name: "Existing home agent",
      systemPrompt: "Use my home.",
      homeMode: "existing",
      workspaceId: "  workspace-1  ",
      newWorkspaceName: "ignored",
      initialAgentsMd: "ignored",
    });

    expect(result.agent.config.home_workspace_id).toBe("workspace-1");
    expect(result.workspace).toBeNull();
  });

  it.each([
    ["name_required", { name: "", systemPrompt: "Prompt" }],
    ["system_prompt_required", { name: "Agent", systemPrompt: "  " }],
  ] as const)("rejects missing required agent input with %s", (code, values) => {
    expect(() =>
      buildAgentSubmission({
        ...emptyAgentFormState(),
        ...values,
      }),
    ).toThrowError(new AgentFormValidationError(code));
  });

  it.each([
    ["", "model-1"],
    ["provider-1", "  "],
  ])("requires both custom model fields", (defaultProvider, defaultModel) => {
    expect(() =>
      buildAgentSubmission({
        ...emptyAgentFormState(),
        name: "Custom model agent",
        systemPrompt: "Use the configured model.",
        defaultModelMode: "custom",
        defaultProvider,
        defaultModel,
      }),
    ).toThrowError(new AgentFormValidationError("default_model_required"));
  });

  it("requires an existing workspace selection", () => {
    expect(() =>
      buildAgentSubmission({
        ...emptyAgentFormState(),
        name: "Invalid home",
        systemPrompt: "Use a home.",
        homeMode: "existing",
        workspaceId: "  ",
      }),
    ).toThrowError(new AgentFormValidationError("home_workspace_required"));
  });

  it.each([
    ["workspace_name_required", "", "# Rules"],
    ["agents_md_required", "New home", "  "],
  ] as const)(
    "validates a newly requested workspace with %s",
    (code, newWorkspaceName, initialAgentsMd) => {
      expect(() =>
        buildAgentSubmission({
          ...emptyAgentFormState(),
          name: "Invalid new home",
          systemPrompt: "Create a home.",
          homeMode: "create",
          newWorkspaceName,
          initialAgentsMd,
        }),
      ).toThrowError(new AgentFormValidationError(code));
    },
  );

  it("rejects a knowledge scope without a collection", () => {
    expect(() =>
      buildAgentSubmission({
        ...emptyAgentFormState(),
        name: "Invalid knowledge",
        systemPrompt: "Use sources.",
        knowledgeScopes: [{ collectionId: "  ", mode: "all", documentIds: [] }],
      }),
    ).toThrowError(new AgentFormValidationError("knowledge_collection_required"));
  });

  it("rejects an empty document subset", () => {
    expect(() =>
      buildAgentSubmission({
        ...emptyAgentFormState(),
        name: "Invalid subset",
        systemPrompt: "Use selected sources.",
        knowledgeScopes: [
          { collectionId: "collection-1", mode: "subset", documentIds: [] },
        ],
      }),
    ).toThrowError(new AgentFormValidationError("knowledge_subset_required"));
  });

  it("rejects duplicate normalized collections before sending an atomic request", () => {
    expect(() =>
      buildAgentSubmission({
        ...emptyAgentFormState(),
        name: "Invalid duplicate",
        systemPrompt: "Use sources.",
        knowledgeScopes: [
          { collectionId: "collection-1", mode: "all", documentIds: [] },
          { collectionId: "  collection-1  ", mode: "subset", documentIds: ["doc-1"] },
        ],
      }),
    ).toThrowError(new AgentFormValidationError("knowledge_collection_duplicate"));
  });

  it("keeps the original subset order while removing duplicate documents", () => {
    const result = buildAgentSubmission({
      ...emptyAgentFormState(),
      name: "Subset agent",
      systemPrompt: "Use selected documents.",
      knowledgeScopes: [
        {
          collectionId: "collection-1",
          mode: "subset",
          documentIds: ["doc-3", "doc-1", "doc-3", "doc-2", "doc-1"],
        },
      ],
    });

    expect(result.agent.config.knowledge_scopes).toEqual([
      {
        collection_id: "collection-1",
        document_ids: ["doc-3", "doc-1", "doc-2"],
      },
    ]);
  });
});

describe("agentToFormState", () => {
  it("maps edit config and clones every document subset", () => {
    const agent: Agent = {
      id: "agent-1",
      name: "Configured agent",
      scope: "private",
      can_manage: true,
      is_active: true,
      config: {
        system_prompt: "Use configured resources.",
        default_model: { provider: "qwen", model: "qwen3.7-plus" },
        home_workspace_id: "workspace-1",
        knowledge_scopes: [
          { collection_id: "collection-all", document_ids: null },
          { collection_id: "collection-subset", document_ids: ["doc-1", "doc-2"] },
        ],
      },
      created_at: "2026-07-13T00:00:00Z",
      updated_at: "2026-07-13T00:00:00Z",
    };

    const state = agentToFormState(agent);

    expect(state).toEqual({
      name: "Configured agent",
      systemPrompt: "Use configured resources.",
      defaultModelMode: "custom",
      defaultProvider: "qwen",
      defaultModel: "qwen3.7-plus",
      homeMode: "existing",
      workspaceId: "workspace-1",
      newWorkspaceName: "",
      initialAgentsMd: "",
      knowledgeScopes: [
        { collectionId: "collection-all", mode: "all", documentIds: [] },
        {
          collectionId: "collection-subset",
          mode: "subset",
          documentIds: ["doc-1", "doc-2"],
        },
      ],
    });
    state.knowledgeScopes[1].documentIds.push("doc-3");
    expect(agent.config.knowledge_scopes[1].document_ids).toEqual(["doc-1", "doc-2"]);
  });

  it("returns a new empty state for a new agent", () => {
    const first = agentToFormState(null);
    const second = agentToFormState(null);

    expect(first).toEqual(emptyAgentFormState());
    expect(first).not.toBe(second);
    expect(first.knowledgeScopes).not.toBe(second.knowledgeScopes);
  });
});
