import type { KnowledgeCollectionConfig } from "@/lib/types";

// These values mirror the server's public request contracts. The server remains
// authoritative; the client uses them only to provide immediate form feedback.
export const MIN_PASSWORD_LENGTH = 6;
export const MAX_PASSWORD_LENGTH = 256;
export const MIN_USERNAME_LENGTH = 3;
export const MAX_USERNAME_LENGTH = 80;
export const MAX_RESOURCE_NAME_LENGTH = 120;
export const MAX_DISPLAY_NAME_LENGTH = MAX_RESOURCE_NAME_LENGTH;
export const MAX_PROMPT_LENGTH = 100_000;

export const MAX_ATTACHMENTS_PER_MESSAGE = 10;

export const MAX_KNOWLEDGE_COLLECTION_NAME_LENGTH = MAX_RESOURCE_NAME_LENGTH;
export const KNOWLEDGE_COLLECTION_LIMITS = {
  chunkSizeMin: 200,
  chunkSizeMax: 4_000,
  chunkOverlapMin: 0,
  chunkOverlapMax: 1_000,
  topKMin: 1,
  topKMax: 10,
  scoreThresholdMin: 0,
  scoreThresholdMax: 1,
  weightMin: 0,
  weightMax: 1,
} as const;

export const MAX_KNOWLEDGE_SCOPES = 20;
export const MAX_DOCUMENTS_PER_SCOPE = 100;

export const DEFAULT_KNOWLEDGE_COLLECTION_CONFIG: KnowledgeCollectionConfig = {
  retriever_type: "elasticsearch",
  retrieval_mode: "vector",
  chunk_size: 900,
  chunk_overlap: 120,
  top_k: 5,
  score_threshold: 0.5,
  vector_weight: 0.7,
  keyword_weight: 0.3,
};
