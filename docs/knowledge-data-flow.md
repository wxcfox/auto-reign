# Knowledge Base Data Flow

This document describes the current Auto Reign knowledge-base flow for uploaded
materials, learning notes, workspace projection, chunking, embeddings, vector
indexing, and retrieval.

```mermaid
flowchart TD
  A[User uploads material or records a learning note] --> B{Input type}
  B -->|Markdown or TXT| C[Store original source file under DATA_DIR/sources/documents]
  B -->|PDF or DOCX| D[Store original source file under DATA_DIR/sources/documents]
  D --> E[Extract readable text into sources/extracted when available]
  B -->|Learning note| F[Store original note as source material]
  F --> G[LLM summarizes note into a managed knowledge Markdown file]
  C --> H[Organize source into candidate profile, target profile, or knowledge]
  E --> H
  G --> I[Rebuild workspace projection]
  H --> I
  I --> J[Scan workspace files and sidecar metadata]
  J --> K[Upsert artifact metadata into MySQL]
  K --> L[Rebuild vector index]
  L --> M[Read indexable text from text sources, extracted text, knowledge, and practice]
  M --> N[Split text into overlapping chunks]
  N --> O[Generate embeddings with configured embedding provider]
  O --> P[Upsert chunk vectors and metadata into Qdrant]
  P --> Q[Persist active vector collection in workspace settings]
  Q --> R[Interview service builds retrieval query from current context]
  R --> S[Embed retrieval query]
  S --> T[Search active Qdrant collection]
  T --> U[Inject retrieved snippets into interview question, feedback, or summary prompts]
```

## Current Storage Responsibilities

- `DATA_DIR/sources/documents/` stores original uploaded files and original
  learning-note sources. Source files keep the user's original filename in
  metadata and display it in the library.
- `DATA_DIR/sources/extracted/` stores extracted text for PDF and DOCX inputs
  when readable text can be extracted.
- `DATA_DIR/knowledge/`, `DATA_DIR/profile/`, `DATA_DIR/practice/`,
  `DATA_DIR/state/`, and `DATA_DIR/reports/` store managed Markdown artifacts.
- MySQL stores the projection of workspace artifacts, processing status, index
  status, revisions, and session/report metadata.
- Qdrant stores searchable chunk vectors. The active Qdrant collection can be
  rebuilt from the filesystem workspace and MySQL artifact projection.

## Indexing Rules

- Markdown and TXT source files are indexed directly from the original source.
- PDF and DOCX source files are not indexed directly; their extracted Markdown
  artifact is indexed when extraction succeeds.
- Knowledge and practice Markdown artifacts are indexed from their body content.
- Candidate profile, target profile, plans, reports, and mastery state remain
  visible in the library but are not currently part of the vector index.
- A deleted library artifact removes the matching workspace file and the
  projection is rebuilt. Index rebuild then removes stale vector content by
  publishing a fresh active collection.
