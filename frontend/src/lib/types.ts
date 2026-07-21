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

export interface Attachment {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  message_id: string | null;
  created_at: string;
}

export interface ConversationMessage {
  id: string;
  role: "assistant" | "user";
  status: "pending" | "streaming" | "completed" | "failed";
  content: string;
  attachments: Attachment[];
  provider: string | null;
  model: string | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface ConversationHistoryItem {
  id: string;
  title: string;
  href: string;
  agent: {
    id: string | null;
    name: string;
    is_available: boolean;
  };
  model_override: ModelRef | null;
  status: "idle" | "generating";
  started_at: string;
  updated_at: string;
  last_message: string;
}

export interface ConversationListResponse {
  conversations: ConversationHistoryItem[];
}

export interface ConversationDetailResponse extends ConversationHistoryItem {
  messages: ConversationMessage[];
}

export interface ConversationDeleteResponse {
  id: string;
  status: "deleted";
}

export interface ConversationStreamResult {
  conversation_id: string;
  message: ConversationMessage;
}

export interface AcceptedGeneration {
  conversation_id: string;
  user_message_id: string | null;
  assistant_message_id: string;
  attachment_ids: string[];
}
