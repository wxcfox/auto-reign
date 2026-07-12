import type {
  AgentConfig,
  AgentResource,
  ResourceWriteRequest,
  WorkspaceConfig,
} from "@/lib/types";

export type HomeMode = "none" | "existing" | "create";
export type KnowledgeMode = "all" | "subset";

export interface KnowledgeScopeDraft {
  collectionId: string;
  mode: KnowledgeMode;
  documentIds: string[];
}

export interface AgentFormState {
  name: string;
  systemPrompt: string;
  defaultModelMode: "follow" | "custom";
  defaultProvider: string;
  defaultModel: string;
  homeMode: HomeMode;
  workspaceId: string;
  newWorkspaceName: string;
  initialAgentsMd: string;
  knowledgeScopes: KnowledgeScopeDraft[];
}

export type AgentFormErrorCode =
  | "name_required"
  | "system_prompt_required"
  | "default_model_required"
  | "home_workspace_required"
  | "workspace_name_required"
  | "agents_md_required"
  | "knowledge_collection_required"
  | "knowledge_collection_duplicate"
  | "knowledge_subset_required";

export class AgentFormValidationError extends Error {
  constructor(readonly code: AgentFormErrorCode) {
    super(code);
    this.name = "AgentFormValidationError";
  }
}

export interface AgentSubmission {
  agent: ResourceWriteRequest<AgentConfig>;
  workspace: ResourceWriteRequest<WorkspaceConfig> | null;
}

export function emptyAgentFormState(): AgentFormState {
  return {
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
  };
}

export function agentToFormState(agent: AgentResource | null): AgentFormState {
  if (agent === null) {
    return emptyAgentFormState();
  }
  return {
    name: agent.name,
    systemPrompt: agent.config.system_prompt,
    defaultModelMode: agent.config.default_model === null ? "follow" : "custom",
    defaultProvider: agent.config.default_model?.provider ?? "",
    defaultModel: agent.config.default_model?.model ?? "",
    homeMode: agent.config.home_workspace_id === null ? "none" : "existing",
    workspaceId: agent.config.home_workspace_id ?? "",
    newWorkspaceName: "",
    initialAgentsMd: "",
    knowledgeScopes: agent.config.knowledge_scopes.map((scope) => ({
      collectionId: scope.collection_id,
      mode: scope.document_ids === null ? "all" : "subset",
      documentIds: scope.document_ids === null ? [] : [...scope.document_ids],
    })),
  };
}

function required(value: string, code: AgentFormErrorCode): string {
  const normalized = value.trim();
  if (!normalized) {
    throw new AgentFormValidationError(code);
  }
  return normalized;
}

export function buildAgentSubmission(state: AgentFormState): AgentSubmission {
  const name = required(state.name, "name_required");
  const systemPrompt = required(state.systemPrompt, "system_prompt_required");
  const defaultModel =
    state.defaultModelMode === "follow"
      ? null
      : {
          provider: required(state.defaultProvider, "default_model_required"),
          model: required(state.defaultModel, "default_model_required"),
        };

  let homeWorkspaceId: string | null = null;
  let workspace: ResourceWriteRequest<WorkspaceConfig> | null = null;
  if (state.homeMode === "existing") {
    homeWorkspaceId = required(state.workspaceId, "home_workspace_required");
  } else if (state.homeMode === "create") {
    workspace = {
      name: required(state.newWorkspaceName, "workspace_name_required"),
      config: {
        workspace_type: "agent_home",
        initial_agents_md: required(state.initialAgentsMd, "agents_md_required"),
      },
    };
  }

  const seenCollections = new Set<string>();
  const knowledgeScopes = state.knowledgeScopes.map((draft) => {
    const collectionId = required(
      draft.collectionId,
      "knowledge_collection_required",
    );
    if (seenCollections.has(collectionId)) {
      throw new AgentFormValidationError("knowledge_collection_duplicate");
    }
    seenCollections.add(collectionId);
    if (draft.mode === "all") {
      return { collection_id: collectionId, document_ids: null };
    }
    const documentIds = [...new Set(draft.documentIds)];
    if (documentIds.length === 0) {
      throw new AgentFormValidationError("knowledge_subset_required");
    }
    return { collection_id: collectionId, document_ids: documentIds };
  });

  return {
    agent: {
      name,
      config: {
        system_prompt: systemPrompt,
        default_model: defaultModel,
        home_workspace_id: homeWorkspaceId,
        knowledge_scopes: knowledgeScopes,
      },
    },
    workspace,
  };
}
