import type {
  AnswerFeedback,
  AuthTokenResponse,
  ConversationDeleteResponse,
  ConversationDetailResponse,
  ConversationHistoryItem,
  ConversationListResponse,
  FollowUpFeedback,
  HealthResponse,
  InterviewConfig,
  InterviewConfigResponse,
  InterviewSessionCreatedResponse,
  InterviewSessionDetailResponse,
  InterviewSessionFinishResponse,
  LearningNoteRequest,
  LearningNoteResponse,
  ModelListResponse,
  PreparationTasksResponse,
  RealInterviewRecordRequest,
  RealInterviewRecordResponse,
  ReportDetailResponse,
  ReportListResponse,
  UploadMaterialsResponse,
  User,
  WorkspaceArtifactDetail,
  WorkspaceArtifactListResponse,
  WorkspaceArtifactSummary,
  WorkspaceStatusResponse,
} from "./types";
import { ApiError, throwApiError } from "./api-error";
import { clearAuthToken, getAuthToken } from "./auth";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  addAuthHeader(headers);
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    handleUnauthorized(response);
    await throwApiError(response, `Request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function uploadMaterials(files: File[]): Promise<UploadMaterialsResponse> {
  const body = new FormData();
  for (const file of files) {
    body.append("files", file);
  }
  const headers = new Headers();
  addAuthHeader(headers);
  const response = await fetch(`${API_BASE_URL}/api/workspace/materials/upload`, {
    method: "POST",
    headers,
    body,
  });
  if (!response.ok) {
    handleUnauthorized(response);
    await throwApiError(response, `Upload failed with ${response.status}`);
  }
  return response.json() as Promise<UploadMaterialsResponse>;
}

export function getWorkspaceStatus(): Promise<WorkspaceStatusResponse> {
  return apiJson<WorkspaceStatusResponse>("/api/workspace");
}

export function getWorkspaceArtifacts(): Promise<WorkspaceArtifactListResponse> {
  return apiJson<WorkspaceArtifactListResponse>("/api/workspace/artifacts");
}

export function getPreparationTasks(): Promise<PreparationTasksResponse> {
  return apiJson<PreparationTasksResponse>("/api/workspace/preparation-tasks");
}

export function listConversations(): Promise<ConversationListResponse> {
  return apiJson<ConversationListResponse>("/api/conversations");
}

export function getConversation(conversationId: string): Promise<ConversationDetailResponse> {
  return apiJson<ConversationDetailResponse>(`/api/conversations/${conversationId}`);
}

export function renameConversation(
  conversationId: string,
  title: string,
): Promise<ConversationHistoryItem> {
  return apiJson<ConversationHistoryItem>(`/api/conversations/${conversationId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function deleteConversation(conversationId: string): Promise<ConversationDeleteResponse> {
  return apiJson<ConversationDeleteResponse>(`/api/conversations/${conversationId}`, {
    method: "DELETE",
  });
}

export function recordRealInterview(
  payload: RealInterviewRecordRequest,
): Promise<RealInterviewRecordResponse> {
  return apiJson<RealInterviewRecordResponse>("/api/workspace/real-interview-records", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getWorkspaceArtifact(artifactId: string): Promise<WorkspaceArtifactDetail> {
  return apiJson<WorkspaceArtifactDetail>(`/api/workspace/artifacts/${artifactId}`);
}

export function replaceWorkspaceArtifactBody(
  artifactId: string,
  expectedRevision: number,
  body: string,
): Promise<WorkspaceArtifactSummary> {
  return apiJson<WorkspaceArtifactSummary>(`/api/workspace/artifacts/${artifactId}/body`, {
    method: "PUT",
    body: JSON.stringify({ expected_revision: expectedRevision, body }),
  });
}

export function deleteWorkspaceArtifact(
  artifactId: string,
): Promise<{ id: string; status: string }> {
  return apiJson<{ id: string; status: string }>(`/api/workspace/artifacts/${artifactId}`, {
    method: "DELETE",
  });
}

export function recordLearningNoteStream(
  payload: LearningNoteRequest,
  callbacks: StreamCallbacks,
): Promise<LearningNoteResponse> {
  return apiStream<LearningNoteResponse>(
    "/api/workspace/learning-notes/stream",
    payload,
    callbacks,
  );
}

export function getModels(): Promise<ModelListResponse> {
  return apiJson<ModelListResponse>("/api/models");
}

export function getLastInterviewConfig(): Promise<InterviewConfigResponse> {
  return apiJson<InterviewConfigResponse>("/api/interview-configs/last");
}

export function saveLastInterviewConfig(
  config: InterviewConfig,
): Promise<InterviewConfigResponse> {
  return apiJson<InterviewConfigResponse>("/api/interview-configs/last", {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export function getInterviewSession(sessionId: string): Promise<InterviewSessionDetailResponse> {
  return apiJson<InterviewSessionDetailResponse>(`/api/interview-sessions/${sessionId}`);
}

export type StreamCallbacks = {
  onDelta: (text: string) => void;
};

type StreamErrorPayload = {
  code?: string;
  message?: string;
  status_code?: number;
};

async function apiStream<T>(
  path: string,
  body: unknown,
  callbacks: StreamCallbacks,
): Promise<T> {
  const headers = new Headers();
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  addAuthHeader(headers);
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    handleUnauthorized(response);
    await throwApiError(response, `Request failed with ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Streaming response did not include a body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: T | null = null;

  const processFrame = (frame: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith("event:")) {
        event = line.slice("event:".length).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice("data:".length).trimStart());
      }
    }
    if (dataLines.length === 0) {
      return;
    }

    const data = JSON.parse(dataLines.join("\n")) as unknown;
    if (event === "delta") {
      const delta = data as { text?: unknown };
      if (typeof delta.text === "string" && delta.text.length > 0) {
        callbacks.onDelta(delta.text);
      }
      return;
    }
    if (event === "result") {
      result = data as T;
      return;
    }
    if (event === "error") {
      const error = data as StreamErrorPayload;
      throw new ApiError(error.message || error.code || "Streaming request failed.", {
        code: error.code,
        status: error.status_code ?? 502,
      });
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex >= 0) {
      const frame = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      processFrame(frame);
      separatorIndex = buffer.indexOf("\n\n");
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) {
    processFrame(buffer);
  }
  if (result === null) {
    throw new Error("Streaming response completed without a result.");
  }
  return result;
}

export function createInterviewSessionStream(
  config: InterviewConfig,
  callbacks: StreamCallbacks,
): Promise<InterviewSessionCreatedResponse> {
  return apiStream<InterviewSessionCreatedResponse>(
    "/api/interview-sessions/stream",
    config,
    callbacks,
  );
}

export function submitAnswerStream(
  sessionId: string,
  answer: string,
  callbacks: StreamCallbacks,
): Promise<AnswerFeedback> {
  return apiStream<AnswerFeedback>(
    `/api/interview-sessions/${sessionId}/answer/stream`,
    { answer },
    callbacks,
  );
}

export function submitFollowUpAnswerStream(
  sessionId: string,
  answer: string,
  callbacks: StreamCallbacks,
): Promise<FollowUpFeedback> {
  return apiStream<FollowUpFeedback>(
    `/api/interview-sessions/${sessionId}/follow-up-answer/stream`,
    { answer },
    callbacks,
  );
}

export function nextQuestionStream(
  sessionId: string,
  callbacks: StreamCallbacks,
  intent = "",
): Promise<InterviewSessionCreatedResponse> {
  return apiStream<InterviewSessionCreatedResponse>(
    `/api/interview-sessions/${sessionId}/next-question/stream`,
    intent ? { intent } : undefined,
    callbacks,
  );
}

export function finishInterviewSessionStream(
  sessionId: string,
  callbacks: StreamCallbacks,
): Promise<InterviewSessionFinishResponse> {
  return apiStream<InterviewSessionFinishResponse>(
    `/api/interview-sessions/${sessionId}/finish/stream`,
    undefined,
    callbacks,
  );
}

export function getHealth(): Promise<HealthResponse> {
  return apiJson<HealthResponse>("/api/health");
}

export function getReports(): Promise<ReportListResponse> {
  return apiJson<ReportListResponse>("/api/reports");
}

export function getReport(reportId: string): Promise<ReportDetailResponse> {
  return apiJson<ReportDetailResponse>(`/api/reports/${reportId}`);
}

export function registerUser(username: string, password: string): Promise<AuthTokenResponse> {
  return apiJson<AuthTokenResponse>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function loginUser(username: string, password: string): Promise<AuthTokenResponse> {
  return apiJson<AuthTokenResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export function getCurrentUser(): Promise<User> {
  return apiJson<User>("/api/auth/me");
}

function addAuthHeader(headers: Headers) {
  const token = getAuthToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
}

function handleUnauthorized(response: Response) {
  if (response.status !== 401) {
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
  if (currentPath === "/login" || currentPath === "/register") {
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
