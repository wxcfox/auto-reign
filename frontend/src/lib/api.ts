import type {
  AdminUser,
  AdminUserCreateRequest,
  AdminUserListResponse,
  AgentConfig,
  AgentListResponse,
  AgentResource,
  AuthTokenResponse,
  KnowledgeCollectionConfig,
  KnowledgeCollectionListResponse,
  KnowledgeCollectionResource,
  KnowledgeDeletePending,
  KnowledgeDocument,
  KnowledgeDocumentContent,
  ModelListResponse,
  ModelRef,
  ResourceDeleteResponse,
  ResourceListScope,
  ResourceScope,
  ResourceUpdateRequest,
  ResourceWriteRequest,
  SubtaskContextBrief,
  SubtaskContextBriefList,
  TaskDetailResponse,
  TaskHistoryItemResponse,
  TaskListResponse,
  User,
  WorkspaceConfig,
  WorkspaceFileContent,
  WorkspaceFileCreateRequest,
  WorkspaceFileList,
  WorkspaceFileWriteRequest,
  WorkspaceListResponse,
  WorkspaceResource,
  WorkspaceScope,
} from "./types";
import { throwApiError } from "./api-error";
import { clearAuthToken, getAuthToken } from "./auth";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");

type ApiRequestOptions = RequestInit & {
  acceptedStatuses?: readonly number[];
};

async function requestApiResponse(
  path: string,
  options: ApiRequestOptions = {},
): Promise<Response> {
  const { acceptedStatuses = [], ...init } = options;
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const requestToken = addAuthHeader(headers);
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok && !acceptedStatuses.includes(response.status)) {
    handleUnauthorized(response, requestToken);
    await throwApiError(response, `Request failed with ${response.status}`);
  }
  return response;
}

async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const response = await requestApiResponse(path, options);
  return response.json() as Promise<T>;
}

async function apiRequestNullable<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T | null> {
  const response = await requestApiResponse(path, options);
  if (response.status === 204) {
    return null;
  }
  return response.json() as Promise<T>;
}

export function setupAdminPassword(password: string): Promise<AuthTokenResponse> {
  return apiRequest<AuthTokenResponse>("/api/auth/admin-password/setup", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function loginUser(username: string, password: string): Promise<AuthTokenResponse> {
  return apiRequest<AuthTokenResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function getCurrentUser(): Promise<User> {
  return apiRequest<User>("/api/auth/me");
}

export function getModels(): Promise<ModelListResponse> {
  return apiRequest<ModelListResponse>("/api/models");
}

type ResourceListOptions = {
  includeInactive?: boolean;
};

function resourceListPath(
  basePath: string,
  scope: ResourceListScope,
  options: ResourceListOptions,
): string {
  const includeInactive = options.includeInactive ? "&include_inactive=true" : "";
  return `${basePath}?scope=${scope}${includeInactive}`;
}

export function listAgents(
  scope: ResourceListScope = "visible",
  options: ResourceListOptions = {},
): Promise<AgentListResponse> {
  return apiRequest<AgentListResponse>(resourceListPath("/api/agents", scope, options));
}

export function getAgent(agentId: string): Promise<AgentResource> {
  return apiRequest<AgentResource>(`/api/agents/${encodeURIComponent(agentId)}`);
}

function projectAgentConfig(config: AgentConfig): AgentConfig {
  return {
    system_prompt: config.system_prompt,
    default_model:
      config.default_model === null
        ? null
        : {
            provider: config.default_model.provider,
            model: config.default_model.model,
          },
    home_workspace_id: config.home_workspace_id,
    knowledge_scopes: config.knowledge_scopes.map((scope) => ({
      collection_id: scope.collection_id,
      document_ids: scope.document_ids === null ? null : [...scope.document_ids],
    })),
  };
}

function projectAgentWrite(
  payload: ResourceWriteRequest<AgentConfig>,
): ResourceWriteRequest<AgentConfig> {
  return {
    name: payload.name,
    config: projectAgentConfig(payload.config),
  };
}

export function createAgent(
  payload: ResourceWriteRequest<AgentConfig>,
): Promise<AgentResource> {
  return apiRequest<AgentResource>("/api/agents", {
    method: "POST",
    body: JSON.stringify(projectAgentWrite(payload)),
  });
}

export function createGlobalAgent(
  payload: ResourceWriteRequest<AgentConfig>,
): Promise<AgentResource> {
  return apiRequest<AgentResource>("/api/admin/agents", {
    method: "POST",
    body: JSON.stringify(projectAgentWrite(payload)),
  });
}

export function updateAgent(
  agentId: string,
  payload: ResourceUpdateRequest<AgentConfig>,
): Promise<AgentResource> {
  const body: ResourceUpdateRequest<AgentConfig> = {
    ...projectAgentWrite(payload),
    is_active: payload.is_active,
  };
  return apiRequest<AgentResource>(`/api/agents/${encodeURIComponent(agentId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteAgent(agentId: string): Promise<ResourceDeleteResponse> {
  return apiRequest<ResourceDeleteResponse>(`/api/agents/${encodeURIComponent(agentId)}`, {
    method: "DELETE",
  });
}

function workspaceMutationBase(scope: WorkspaceScope): string {
  return scope === "global" ? "/api/admin/workspaces" : "/api/workspaces";
}

function workspaceMutationPath(scope: WorkspaceScope, workspaceId: string): string {
  return `${workspaceMutationBase(scope)}/${encodeURIComponent(workspaceId)}`;
}

function workspaceFilePath(scope: WorkspaceScope, workspaceId: string): string {
  return `${workspaceMutationPath(scope, workspaceId)}/files`;
}

function projectWorkspaceWrite(
  payload: ResourceWriteRequest<WorkspaceConfig>,
): ResourceWriteRequest<WorkspaceConfig> {
  return {
    name: payload.name,
    config: {
      workspace_type: "agent_home",
      initial_agents_md: payload.config.initial_agents_md,
    },
  };
}

export function listWorkspaces(
  scope: ResourceListScope,
  options: ResourceListOptions = {},
): Promise<WorkspaceListResponse> {
  return apiRequest<WorkspaceListResponse>(
    resourceListPath("/api/workspaces", scope, options),
  );
}

export function getWorkspace(workspaceId: string): Promise<WorkspaceResource> {
  return apiRequest<WorkspaceResource>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}`,
  );
}

export function createWorkspace(
  scope: WorkspaceScope,
  payload: ResourceWriteRequest<WorkspaceConfig>,
): Promise<WorkspaceResource> {
  return apiRequest<WorkspaceResource>(workspaceMutationBase(scope), {
    method: "POST",
    body: JSON.stringify(projectWorkspaceWrite(payload)),
  });
}

export function updateWorkspace(
  scope: WorkspaceScope,
  workspaceId: string,
  payload: ResourceUpdateRequest<WorkspaceConfig>,
): Promise<WorkspaceResource> {
  const body: ResourceUpdateRequest<WorkspaceConfig> = {
    ...projectWorkspaceWrite(payload),
    is_active: payload.is_active,
  };
  return apiRequest<WorkspaceResource>(workspaceMutationPath(scope, workspaceId), {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteWorkspace(
  scope: WorkspaceScope,
  workspaceId: string,
): Promise<ResourceDeleteResponse> {
  return apiRequest<ResourceDeleteResponse>(workspaceMutationPath(scope, workspaceId), {
    method: "DELETE",
  });
}

export function listWorkspaceFiles(
  scope: WorkspaceScope,
  workspaceId: string,
  directory = "",
): Promise<WorkspaceFileList> {
  return apiRequest<WorkspaceFileList>(
    `${workspaceFilePath(scope, workspaceId)}?directory=${encodeURIComponent(directory)}`,
  );
}

export function readWorkspaceFile(
  scope: WorkspaceScope,
  workspaceId: string,
  path: string,
): Promise<WorkspaceFileContent> {
  return apiRequest<WorkspaceFileContent>(
    `${workspaceFilePath(scope, workspaceId)}/content?path=${encodeURIComponent(path)}`,
  );
}

export function createWorkspaceFile(
  scope: WorkspaceScope,
  workspaceId: string,
  payload: WorkspaceFileCreateRequest,
): Promise<WorkspaceFileContent> {
  const body: WorkspaceFileCreateRequest = {
    path: payload.path,
    content: payload.content,
  };
  return apiRequest<WorkspaceFileContent>(`${workspaceFilePath(scope, workspaceId)}/content`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function writeWorkspaceFile(
  scope: WorkspaceScope,
  workspaceId: string,
  payload: WorkspaceFileWriteRequest,
): Promise<WorkspaceFileContent> {
  const body: WorkspaceFileWriteRequest = {
    path: payload.path,
    content: payload.content,
    expected_etag: payload.expected_etag,
  };
  return apiRequest<WorkspaceFileContent>(`${workspaceFilePath(scope, workspaceId)}/content`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteWorkspaceFile(
  scope: WorkspaceScope,
  workspaceId: string,
  path: string,
): Promise<void> {
  await apiRequestNullable<never>(
    `${workspaceFilePath(scope, workspaceId)}?path=${encodeURIComponent(path)}`,
    { method: "DELETE", acceptedStatuses: [204] },
  );
}

export function listKnowledgeCollections(
  scope: ResourceListScope,
  options: ResourceListOptions = {},
): Promise<KnowledgeCollectionListResponse> {
  return apiRequest<KnowledgeCollectionListResponse>(
    resourceListPath("/api/knowledge-collections", scope, options),
  );
}

export function getKnowledgeCollection(
  collectionId: string,
): Promise<KnowledgeCollectionResource> {
  return apiRequest<KnowledgeCollectionResource>(
    `/api/knowledge-collections/${encodeURIComponent(collectionId)}`,
  );
}

function knowledgeCollectionMutationBase(scope: ResourceScope): string {
  return scope === "global"
    ? "/api/admin/knowledge-collections"
    : "/api/knowledge-collections";
}

function projectKnowledgeCollectionWrite(
  payload: ResourceWriteRequest<KnowledgeCollectionConfig>,
): ResourceWriteRequest<KnowledgeCollectionConfig> {
  return {
    name: payload.name,
    config: {
      retriever_type: payload.config.retriever_type,
      retrieval_mode: payload.config.retrieval_mode,
      chunk_size: payload.config.chunk_size,
      chunk_overlap: payload.config.chunk_overlap,
      top_k: payload.config.top_k,
      score_threshold: payload.config.score_threshold,
      vector_weight: payload.config.vector_weight,
      keyword_weight: payload.config.keyword_weight,
    },
  };
}

export function createKnowledgeCollection(
  scope: ResourceScope,
  payload: ResourceWriteRequest<KnowledgeCollectionConfig>,
): Promise<KnowledgeCollectionResource> {
  return apiRequest<KnowledgeCollectionResource>(knowledgeCollectionMutationBase(scope), {
    method: "POST",
    body: JSON.stringify(projectKnowledgeCollectionWrite(payload)),
  });
}

export function updateKnowledgeCollection(
  scope: ResourceScope,
  collectionId: string,
  payload: ResourceUpdateRequest<KnowledgeCollectionConfig>,
): Promise<KnowledgeCollectionResource> {
  const body: ResourceUpdateRequest<KnowledgeCollectionConfig> = {
    ...projectKnowledgeCollectionWrite(payload),
    is_active: payload.is_active,
  };
  return apiRequest<KnowledgeCollectionResource>(
    `${knowledgeCollectionMutationBase(scope)}/${encodeURIComponent(collectionId)}`,
    { method: "PUT", body: JSON.stringify(body) },
  );
}

export function deleteKnowledgeCollection(
  scope: ResourceScope,
  collectionId: string,
): Promise<ResourceDeleteResponse> {
  return apiRequest<ResourceDeleteResponse>(
    `${knowledgeCollectionMutationBase(scope)}/${encodeURIComponent(collectionId)}`,
    { method: "DELETE" },
  );
}

export function listAdminUsers(): Promise<AdminUserListResponse> {
  return apiRequest<AdminUserListResponse>("/api/admin/users");
}

export function createAdminUser(payload: AdminUserCreateRequest): Promise<AdminUser> {
  const body: AdminUserCreateRequest = {
    username: payload.username,
    display_name: payload.display_name,
    password: payload.password,
  };
  return apiRequest<AdminUser>("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function setAdminUserStatus(
  userId: number,
  isActive: boolean,
): Promise<AdminUser> {
  return apiRequest<AdminUser>(
    `/api/admin/users/${encodeURIComponent(String(userId))}/status`,
    {
      method: "PATCH",
      body: JSON.stringify({ is_active: isActive }),
    },
  );
}

export function resetAdminUserPassword(
  userId: number,
  password: string,
): Promise<AdminUser> {
  return apiRequest<AdminUser>(
    `/api/admin/users/${encodeURIComponent(String(userId))}/reset-password`,
    {
      method: "POST",
      body: JSON.stringify({ password }),
    },
  );
}

function knowledgeDocumentPath(collectionId: string, documentId?: string): string {
  const base = `/api/knowledge-collections/${encodeURIComponent(collectionId)}/documents`;
  return documentId === undefined ? base : `${base}/${encodeURIComponent(documentId)}`;
}

export function uploadKnowledgeDocument(
  collectionId: string,
  file: File,
): Promise<KnowledgeDocument> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<KnowledgeDocument>(knowledgeDocumentPath(collectionId), {
    method: "POST",
    body,
  });
}

export function listKnowledgeDocuments(
  collectionId: string,
  options: { includeInactive?: boolean } = {},
): Promise<{ documents: KnowledgeDocument[] }> {
  const query = options.includeInactive ? "?include_inactive=true" : "";
  return apiRequest<{ documents: KnowledgeDocument[] }>(
    `${knowledgeDocumentPath(collectionId)}${query}`,
  );
}

export function readKnowledgeDocumentContent(
  collectionId: string,
  documentId: string,
): Promise<KnowledgeDocumentContent> {
  return apiRequest<KnowledgeDocumentContent>(
    `${knowledgeDocumentPath(collectionId, documentId)}/content`,
  );
}

export async function downloadKnowledgeDocument(
  collectionId: string,
  documentId: string,
): Promise<Blob> {
  const response = await requestApiResponse(
    `${knowledgeDocumentPath(collectionId, documentId)}/download`,
  );
  return response.blob();
}

export function reindexKnowledgeDocument(
  collectionId: string,
  documentId: string,
): Promise<KnowledgeDocument> {
  return apiRequest<KnowledgeDocument>(
    `${knowledgeDocumentPath(collectionId, documentId)}/reindex`,
    { method: "POST" },
  );
}

export function deleteKnowledgeDocument(
  collectionId: string,
  documentId: string,
): Promise<KnowledgeDeletePending | null> {
  return apiRequestNullable<KnowledgeDeletePending>(
    knowledgeDocumentPath(collectionId, documentId),
    { method: "DELETE", acceptedStatuses: [202, 204] },
  );
}

export function listTasks(): Promise<TaskListResponse> {
  return apiRequest<TaskListResponse>("/api/tasks");
}

export function getTask(taskId: number): Promise<TaskDetailResponse> {
  return apiRequest<TaskDetailResponse>(`/api/tasks/${encodeURIComponent(String(taskId))}`);
}

export function renameTask(taskId: number, name: string): Promise<TaskHistoryItemResponse> {
  return apiRequest<TaskHistoryItemResponse>(`/api/tasks/${encodeURIComponent(String(taskId))}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function deleteTask(taskId: number): Promise<void> {
  await apiRequestNullable<never>(`/api/tasks/${encodeURIComponent(String(taskId))}`, {
    method: "DELETE",
    acceptedStatuses: [204],
  });
}

export function setTaskModel(
  taskId: number,
  modelOverride: ModelRef | null,
): Promise<TaskDetailResponse> {
  return apiRequest<TaskDetailResponse>(
    `/api/tasks/${encodeURIComponent(String(taskId))}/model`,
    {
    method: "PUT",
    body: JSON.stringify({ model_override: modelOverride }),
    },
  );
}

export function listSubtaskContextDrafts(): Promise<SubtaskContextBrief[]> {
  return apiRequest<SubtaskContextBriefList>("/api/subtask-contexts/drafts").then(
    (payload) => payload.items,
  );
}

export async function uploadSubtaskContext(file: File): Promise<SubtaskContextBrief> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<SubtaskContextBrief>("/api/subtask-contexts/attachments", {
    method: "POST",
    body,
  });
}

export async function deleteSubtaskContextDraft(contextId: number): Promise<void> {
  const encodedContextId = encodeURIComponent(String(contextId));
  await apiRequestNullable<never>(`/api/subtask-contexts/${encodedContextId}`, {
    method: "DELETE",
    acceptedStatuses: [204],
  });
}

export async function readSubtaskContextContent(
  contextId: number,
  disposition: "inline" | "attachment",
): Promise<Blob> {
  const encodedContextId = encodeURIComponent(String(contextId));
  const response = await requestApiResponse(
    `/api/subtask-contexts/${encodedContextId}/content?disposition=${disposition}`,
  );
  return response.blob();
}

function addAuthHeader(headers: Headers) {
  const token = getAuthToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return token;
}

function handleUnauthorized(response: Response, requestToken: string | null) {
  if (response.status !== 401 || requestToken === null) {
    return;
  }
  const currentToken = getAuthToken();
  if (currentToken !== null && currentToken !== requestToken) {
    return;
  }
  clearAuthToken();
  redirectToLogin();
}

function redirectToLogin() {
  if (typeof window === "undefined") {
    return;
  }
  const currentPath = window.location.pathname;
  if (currentPath === "/login" || currentPath === "/setup") {
    return;
  }
  const currentUrl = `${currentPath}${window.location.search}`;
  const target = `/login?redirect=${encodeURIComponent(currentUrl || "/")}`;
  try {
    window.location.href = target;
  } catch {
    // JSDOM and some restricted browsers may reject programmatic navigation.
  }
  if (window.location.pathname !== "/login") {
    window.history.replaceState(null, "", target);
  }
}
