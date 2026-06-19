import type { DocumentListResponse, DocumentRecord, DocumentUpdate } from "./types";

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
