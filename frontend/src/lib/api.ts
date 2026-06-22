import type {
  AnswerFeedback,
  DocumentListResponse,
  DocumentRecord,
  DocumentUpdate,
  FinishInterviewResponse,
  FollowUpFeedback,
  HealthResponse,
  InterviewConfig,
  InterviewConfigResponse,
  InterviewSessionCreatedResponse,
  MemoryResponse,
  ModelListResponse,
  ReportDetailResponse,
  ReportListResponse,
  UploadMaterialsResponse,
  WorkspaceArtifactDetail,
  WorkspaceArtifactListResponse,
  WorkspaceArtifactSummary,
  WorkspaceStatusResponse,
} from "./types";
import { throwApiError } from "./api-error";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    await throwApiError(response, `Request failed with ${response.status}`);
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
    await throwApiError(response, `Upload failed with ${response.status}`);
  }
  return response.json() as Promise<DocumentRecord>;
}

export async function uploadMaterials(files: File[]): Promise<UploadMaterialsResponse> {
  const body = new FormData();
  for (const file of files) {
    body.append("files", file);
  }
  const response = await fetch(`${API_BASE_URL}/api/workspace/materials/upload`, {
    method: "POST",
    body,
  });
  if (!response.ok) {
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

export function rebuildWorkspaceIndex(): Promise<{ status: string; collection: string }> {
  return apiJson<{ status: string; collection: string }>("/api/workspace/rebuild-index", {
    method: "POST",
  });
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
): Promise<FollowUpFeedback> {
  return apiJson<FollowUpFeedback>(`/api/interview-sessions/${sessionId}/follow-up-answer`, {
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
