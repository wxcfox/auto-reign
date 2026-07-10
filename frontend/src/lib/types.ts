export type ProviderName = "openai" | "deepseek" | "qwen";

export type InterviewMode =
  | "comprehensive"
  | "project_deep_dive"
  | "knowledge_drill"
  | "weakness_reinforcement";

export type SessionStatus = "active" | "completed" | "cancelled";

export interface User {
  id: number;
  username: string;
  display_name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
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
}

export type ConversationKind = "interview" | "learning";
export type ConversationRole = "assistant" | "system" | "user";

export interface ConversationMessage {
  id: string;
  role: ConversationRole;
  message_type: string;
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface ConversationHistoryItem {
  id: string;
  kind: ConversationKind;
  title: string;
  href: string;
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

export interface HealthResponse {
  status: "ok";
  storage: {
    mysql: string;
    qdrant: string;
  };
  providers: Record<ProviderName, boolean>;
  workspace?: {
    initialized: boolean;
  };
}

export interface WorkspaceStatusResponse {
  schema_version: number;
  language: "en" | "zh-CN";
  artifact_count: number;
  initialized: boolean;
}

export interface UploadedSourceRecord {
  artifact_id: string;
  relative_path: string;
  duplicate: boolean;
}

export interface LearningNoteSummary {
  title: string;
  summary: string;
  key_points: string[];
  interview_takeaways: string[];
  follow_up_questions: string[];
}

export interface LearningNoteRequest {
  text: string;
  language: "en" | "zh-CN";
  provider?: ProviderName;
  model?: string;
  conversation_id?: string;
}

export interface LearningNoteResponse {
  conversation_id: string;
  source: UploadedSourceRecord;
  artifact: WorkspaceArtifactSummary;
  summary: LearningNoteSummary;
  card_markdown: string;
}

export interface RealInterviewRecordRequest {
  text: string;
  language: "en" | "zh-CN";
}

export interface RealInterviewRecordResponse {
  raw_artifact: WorkspaceArtifactSummary;
  high_frequency_artifact: WorkspaceArtifactSummary;
  status_artifact: WorkspaceArtifactSummary;
  questions: string[];
  weak_points: string[];
}

export interface UploadMaterialsResponse {
  sources: UploadedSourceRecord[];
}

export interface WorkspaceArtifactSummary {
  id: string;
  kind: string;
  owner: string;
  relative_path: string;
  display_name: string;
  revision: number;
  processing_status: string;
  index_status: string;
  recovery_required: boolean;
  allowed_operations: string[];
  created_at: string;
  updated_at: string;
}

export interface WorkspaceArtifactListResponse {
  artifacts: WorkspaceArtifactSummary[];
}

export interface WorkspaceFileEntry {
  name: string;
  relative_path: string;
  directory: string;
  size_bytes: number;
  created_at: string;
  updated_at: string;
  owner: string;
  kind: string;
  processing_status: string;
  index_status: string;
  recovery_required: boolean;
  allowed_operations: string[];
  artifact_id: string | null;
  artifact_kind: string | null;
}

export interface WorkspaceDirectoryEntry {
  name: string;
  relative_path: string;
  depth: number;
  file_count: number;
  child_directory_count: number;
  created_at: string;
  updated_at: string;
  files: WorkspaceFileEntry[];
}

export interface WorkspaceFilesResponse {
  root: string;
  directories: WorkspaceDirectoryEntry[];
}

export interface WorkspaceFileContentResponse {
  name: string;
  relative_path: string;
  size_bytes: number;
  updated_at: string;
  content: string;
}

export interface WorkspaceArtifactDetail extends WorkspaceArtifactSummary {
  body: string | null;
}

export interface PreparationTask {
  title: string;
  reason: string;
  source_artifact_id: string | null;
  source_relative_path: string | null;
}

export interface PreparationTasksResponse {
  tasks: PreparationTask[];
}

export interface InterviewConfig {
  target_company: string;
  target_role: string;
  job_description: string;
  extra_prompt: string;
  language: "en" | "zh-CN";
  mode: InterviewMode;
  chat_model_provider: ProviderName;
  chat_model: string;
  target_rounds: number;
}

export interface InterviewConfigResponse extends InterviewConfig {
  id: string;
  is_last_used: boolean;
  updated_at: string;
}

export interface InterviewSession {
  id: string;
  config_id: string;
  status: SessionStatus;
  current_round: number;
  started_at: string;
  ended_at: string | null;
  report_path: string | null;
}

export interface InterviewTurn {
  id: string;
  session_id: string;
  round_index: number;
  question: string;
  answer: string | null;
  feedback: string | null;
  missing_points: string[];
  follow_up_question: string | null;
  follow_up_answer: string | null;
  follow_up_feedback: string | null;
  follow_up_missing_points: string[];
  follow_up_weaknesses: string[];
  follow_up_review_suggestions: string[];
  follow_up_better_answer?: string;
  follow_up_mastery_change?: string;
  follow_up_should_write_weakness?: boolean;
  follow_up_should_write_high_frequency?: boolean;
  follow_up_tested_points?: string[];
  weaknesses: string[];
  review_suggestions: string[];
  better_answer?: string;
  mastery_change?: string;
  should_write_weakness?: boolean;
  should_write_high_frequency?: boolean;
  tested_points?: string[];
  retrieved_context_refs: Array<Record<string, string>>;
  created_at: string;
}

export interface InterviewSessionCreatedResponse {
  session: InterviewSession;
  turn: InterviewTurn;
}

export interface InterviewSessionFinishResponse {
  session: InterviewSession;
  report: ReportRecord;
}

export interface InterviewSessionDetailResponse {
  session: InterviewSession;
  config: InterviewConfigResponse;
  turns: InterviewTurn[];
}

export interface AnswerFeedback {
  feedback: string;
  missing_points: string[];
  follow_up_question: string;
  weaknesses: string[];
  review_suggestions: string[];
  better_answer: string;
  mastery_change: string;
  should_write_weakness: boolean;
  should_write_high_frequency: boolean;
  tested_points: string[];
}

export interface FollowUpFeedback {
  feedback: string;
  missing_points: string[];
  weaknesses: string[];
  review_suggestions: string[];
  better_answer: string;
  mastery_change: string;
  should_write_weakness: boolean;
  should_write_high_frequency: boolean;
  tested_points: string[];
}

export interface ReportRecord {
  id: string;
  session_id: string;
  report_path: string;
  summary: string;
  weaknesses: string[];
  created_at: string;
}

export interface ReportListResponse {
  reports: ReportRecord[];
}

export interface ReportDetailResponse {
  report: ReportRecord;
  content: string;
}
