import type {
  AnswerFeedback,
  DocumentListResponse,
  DocumentRecord,
  DocumentUpdate,
  FinishInterviewResponse,
  HealthResponse,
  InterviewConfig,
  InterviewConfigResponse,
  InterviewSessionCreatedResponse,
  InterviewTurn,
  MemoryResponse,
  ModelListResponse,
  ReportDetailResponse,
  ReportListResponse,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(errorBody || `Request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function uploadDocument(file: File): Promise<DocumentRecord> {
  const body = new FormData();
  body.set("file", file);
  const response = await fetch(`${API_BASE_URL}/api/documents/upload`, {
    method: "POST",
    body,
  });
  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(errorBody || `Upload failed with ${response.status}`);
  }
  return response.json() as Promise<DocumentRecord>;
}

export function getDocuments(): Promise<DocumentListResponse> {
  return apiJson<DocumentListResponse>("/api/documents");
}

export function getDocument(documentId: string): Promise<DocumentRecord> {
  return apiJson<DocumentRecord>(`/api/documents/${documentId}`);
}

export function updateDocument(
  documentId: string,
  update: DocumentUpdate,
): Promise<DocumentRecord> {
  return apiJson<DocumentRecord>(`/api/documents/${documentId}`, {
    method: "PATCH",
    body: JSON.stringify(update),
  });
}

export function reindexDocument(documentId: string): Promise<DocumentRecord> {
  return apiJson<DocumentRecord>(`/api/documents/${documentId}/reindex`, {
    method: "POST",
  });
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

export function createInterviewSession(
  config: InterviewConfig,
): Promise<InterviewSessionCreatedResponse> {
  return apiJson<InterviewSessionCreatedResponse>("/api/interview-sessions", {
    method: "POST",
    body: JSON.stringify(config),
  });
}

export function submitAnswer(sessionId: string, answer: string): Promise<AnswerFeedback> {
  return apiJson<AnswerFeedback>(`/api/interview-sessions/${sessionId}/answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}

export function submitFollowUpAnswer(
  sessionId: string,
  answer: string,
): Promise<InterviewTurn> {
  return apiJson<InterviewTurn>(`/api/interview-sessions/${sessionId}/follow-up-answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}

export function nextQuestion(sessionId: string): Promise<InterviewSessionCreatedResponse> {
  return apiJson<InterviewSessionCreatedResponse>(
    `/api/interview-sessions/${sessionId}/next-question`,
    { method: "POST" },
  );
}

export function finishInterview(sessionId: string): Promise<FinishInterviewResponse> {
  return apiJson<FinishInterviewResponse>(`/api/interview-sessions/${sessionId}/finish`, {
    method: "POST",
  });
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

export function getMemory(): Promise<MemoryResponse> {
  return apiJson<MemoryResponse>("/api/memory");
}
