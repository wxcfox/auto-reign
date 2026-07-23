export type ProviderName = string;
export type ResourceScope = "global" | "private";
export type ResourceListScope = "visible" | "owned" | "global";
export type WorkspaceScope = ResourceScope;

export interface ModelRef {
  provider: string;
  model: string;
}

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "user";
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminUser {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "user";
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminUserListResponse {
  users: AdminUser[];
}

export interface AdminUserCreateRequest {
  username: string;
  display_name: string;
  password: string;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: "bearer";
  user: User;
}

export interface ModelProvider {
  provider: ProviderName;
  models: string[];
}

export interface ModelListResponse {
  providers: ModelProvider[];
  default: ModelRef | null;
}

export interface KnowledgeScope {
  collection_id: string;
  document_ids: string[] | null;
}

export interface AgentConfig {
  system_prompt: string;
  default_model: ModelRef | null;
  home_workspace_id: string | null;
  knowledge_scopes: KnowledgeScope[];
}

export interface ResourceEnvelope<TConfig> {
  id: string;
  name: string;
  scope: ResourceScope;
  can_manage: boolean;
  is_active: boolean;
  config: TConfig;
  created_at: string;
  updated_at: string;
}

export interface ResourceWriteRequest<TConfig> {
  name: string;
  config: TConfig;
}

export interface ResourceUpdateRequest<TConfig> extends ResourceWriteRequest<TConfig> {
  is_active: boolean;
}

export interface ResourceDeleteResponse {
  id: string;
  status: "deleted";
}

export type Agent = ResourceEnvelope<AgentConfig>;
export type AgentResource = Agent;

export interface AgentListResponse {
  agents: AgentResource[];
}

export interface WorkspaceConfig {
  workspace_type: "agent_home";
  initial_agents_md: string;
}

export type Workspace = ResourceEnvelope<WorkspaceConfig>;
export type WorkspaceResource = Workspace;

export interface WorkspaceListResponse {
  workspaces: WorkspaceResource[];
}

export interface WorkspaceFileItem {
  path: string;
  name: string;
  is_directory: boolean;
  size_bytes: number | null;
  etag: string | null;
}

export interface WorkspaceFileContent extends WorkspaceFileItem {
  is_directory: false;
  content: string;
}

export interface WorkspaceFileList {
  directory: string;
  items: WorkspaceFileItem[];
}

export interface WorkspaceFileCreateRequest {
  path: string;
  content: string;
}

export interface WorkspaceFileWriteRequest extends WorkspaceFileCreateRequest {
  expected_etag: string;
}

export type KnowledgeDocumentStatus =
  | "uploaded"
  | "queued"
  | "processing"
  | "ready"
  | "failed";

export type KnowledgeRetrieverType = "elasticsearch" | "qdrant";
export type KnowledgeRetrievalMode = "vector" | "keyword" | "hybrid";

export interface KnowledgeCollectionConfig {
  retriever_type: KnowledgeRetrieverType;
  retrieval_mode: KnowledgeRetrievalMode;
  chunk_size: number;
  chunk_overlap: number;
  top_k: number;
  score_threshold: number;
  vector_weight: number;
  keyword_weight: number;
}

export type KnowledgeCollection = ResourceEnvelope<KnowledgeCollectionConfig>;
export type KnowledgeCollectionResource = KnowledgeCollection;

export interface KnowledgeCollectionListResponse {
  collections: KnowledgeCollectionResource[];
}

export interface KnowledgeDocument {
  id: string;
  collection_id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  status: KnowledgeDocumentStatus;
  index_generation: number;
  error_code: string | null;
  error_message: string | null;
  is_active: boolean;
  indexed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocumentContent {
  document_id: string;
  content: string;
}

export interface KnowledgeDeletePending {
  document_id: string;
  status: "cleanup_pending";
}

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export type TaskStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
export type SubtaskStatus = TaskStatus;
export type SubtaskRole = "USER" | "ASSISTANT";

export type SubtaskContextType = "attachment" | "knowledge_base" | "selected_documents";
export type SubtaskContextStatus =
  | "pending"
  | "uploading"
  | "parsing"
  | "ready"
  | "empty"
  | "failed";

export interface SubtaskContextBrief {
  id: number;
  context_type: SubtaskContextType;
  name: string;
  status: SubtaskContextStatus;
  mime_type: string | null;
  file_extension: string | null;
  file_size: number | null;
  text_length: number;
  type_data: JsonObject;
}

export interface SubtaskContextBriefList {
  items: SubtaskContextBrief[];
}

export interface AssistantToolCall {
  id: string;
  type: "function";
  function: {
    name: string;
    /** Canonical JSON object serialized by the backend. */
    arguments: string;
  };
}

export interface AssistantChainMessage {
  role: "assistant";
  content: string | JsonObject[] | null;
  tool_calls?: AssistantToolCall[];
  reasoning_content?: string;
  model_info?: {
    provider: string | null;
    model: string | null;
  };
  compacted?: boolean;
  summary_compacted?: boolean;
  compaction_version?: number | null;
}

export interface ToolChainMessage {
  role: "tool";
  tool_call_id: string;
  name: string;
  content: string;
  is_error?: boolean;
}

export type MessageChainItem = AssistantChainMessage | ToolChainMessage;

export interface TextChatBlock {
  id: string;
  type: "text";
  content: string;
  status: "streaming" | "done";
  timestamp: string;
}

export interface ToolChatBlock {
  id: string;
  type: "tool";
  tool_use_id: string;
  tool_name: string;
  tool_input: JsonObject;
  tool_output?: JsonValue;
  status: "generating_arguments" | "pending" | "done" | "error";
  timestamp: string;
}

export type ChatBlock = TextChatBlock | ToolChatBlock;

export interface ContextCompaction {
  message_index: number;
  compacted: boolean;
  summary_compacted: boolean;
  version: number | null;
}

interface AssistantResultBase {
  value: string;
  blocks: ChatBlock[];
  context_compactions: ContextCompaction[];
  sources: JsonValue[];
  termination_reason: string | null;
}

export interface AssistantCompletedResult extends AssistantResultBase {
  messages_chain: MessageChainItem[];
}

export interface AssistantPartialResult extends AssistantResultBase {
  messages_chain?: never;
}

/** Safe projection returned for failed/cancelled Subtasks in Task history. */
export interface AssistantHistoryResult {
  value?: string;
  messages_chain: MessageChainItem[];
  blocks?: never;
  context_compactions?: never;
  sources?: never;
  termination_reason?: never;
}

export type AssistantResult =
  | AssistantCompletedResult
  | AssistantPartialResult
  | AssistantHistoryResult;

export interface Subtask {
  id: number;
  task_id: number;
  role: SubtaskRole;
  message_id: number;
  parent_id: number | null;
  prompt: string;
  status: SubtaskStatus;
  progress: number;
  result: AssistantResult | null;
  error_message: string | null;
  contexts: SubtaskContextBrief[];
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface TaskAgentResponse {
  id: string | null;
  name: string;
  is_available: boolean;
}

export interface TaskHistoryItemResponse {
  id: number;
  name: string;
  href: string;
  agent: TaskAgentResponse;
  model_override: ModelRef | null;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  last_message: string;
}

export interface TaskListResponse {
  tasks: TaskHistoryItemResponse[];
}

export interface TaskDetailResponse extends TaskHistoryItemResponse {
  subtasks: Subtask[];
}
