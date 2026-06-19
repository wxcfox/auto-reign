# auto-reign v1 Design Spec

## Overview

auto-reign v1 is a local, single-user AI mock interview and knowledge memory system. It helps one user upload personal Markdown/TXT materials, build a RAG knowledge base, run role-specific mock interviews, receive feedback, and maintain persistent memory about weaknesses, interview history, and learning profile.

The first version is intentionally local-first and simple. It does not include login, permissions, multi-user isolation, multi-agent orchestration, audio/video interviews, PDF/Word parsing, API key entry in the frontend, API key storage in the database, Redis, Celery, MySQL, or a complex scoring system.

## Product Scope

The v1 user flow is:

1. User uploads Markdown/TXT documents such as resumes, project notes, study notes, interview experiences, and error-prone topics.
2. Backend synchronously saves the original file, calls AI to analyze the document, and generates a title, summary, tags, knowledge points, and weakness candidates.
3. Backend writes document metadata to SQLite, stores original Markdown/TXT files locally, chunks content, generates embeddings, and indexes chunks in Chroma.
4. User configures target company, target role, job description, extra prompt, interview mode, target rounds, and chat model.
5. Backend retrieves relevant personal knowledge and memory, then generates a mock interview question.
6. User answers. AI returns feedback, missing points, follow-up question, weaknesses, and review suggestions.
7. User can answer the follow-up, move to the next question, or end the session at any time.
8. On finish, backend generates a Markdown review report and updates long-term memory files.
9. Later interviews retrieve uploaded documents, reports, and memory files to reinforce weak areas.

v1 success means a user can start the system with Docker Compose, open the web UI locally, upload Markdown/TXT materials, complete one knowledge-grounded mock interview, and see a generated report plus updated memory files.

## Confirmed Decisions

- Usage mode: local single-user application.
- Document processing: synchronous in v1, with service boundaries that can later support background jobs.
- Knowledge base: one default collection in the UI; data model keeps a `collection` field for future expansion.
- Embedding: independent embedding configuration, defaulting to OpenAI `text-embedding-3-small`.
- Chat models: OpenAI, DeepSeek, and Qwen selectable only when corresponding backend environment variables are configured.
- API keys: backend environment variables only; never stored in frontend state, browser storage, SQLite, Chroma metadata, or Markdown files.
- Interview flow: hybrid rounds. User sets target rounds, can end anytime, and each round supports main question, answer, feedback, follow-up, and optional follow-up answer.
- Turn storage: structured fields for questions, answers, feedback, missing points, follow-ups, weaknesses, and review suggestions.
- Memory update: each memory file has a current summary section at the top and appended historical records below.
- Architecture: backend owns application workflows; frontend owns interaction and presentation.
- Pages: Dashboard, Library, Document Detail, Interview, Review & Memory.
- Interview modes: comprehensive, project deep dive, knowledge drill, and weakness reinforcement.

## Technical Architecture

Frontend uses Next.js, React, and TypeScript. It provides the local web interface for uploading files, browsing the knowledge library, editing AI-generated document metadata, configuring interviews, running chat-like interview sessions, and reading reports and memory files. It does not handle model credentials.

Backend uses FastAPI and Pydantic. It owns business workflows and exposes HTTP APIs consumed by the frontend. The backend is organized around domain services:

- `DocumentService`: upload validation, original file storage, AI document analysis, metadata persistence, and reindex triggers.
- `RagService`: text splitting, embedding, Chroma writes, Chroma deletes/reindexing, and retrieval.
- `InterviewService`: interview configuration, session state, question generation, answer evaluation, follow-up handling, and round progression.
- `MemoryService`: Markdown report generation, long-term memory updates, and memory/report reindexing.
- `ModelService`: provider adapters for OpenAI, DeepSeek, Qwen chat models, plus OpenAI embeddings.
- `ConfigService`: environment-driven model availability, data path settings, and last-used interview configuration.

Storage is local:

- SQLite stores structured records and workflow state.
- Markdown files store uploaded source files, generated reports, and long-term memory.
- Chroma stores vector indexes for uploaded documents, reports, and memory files.

Docker Compose runs two services:

- `backend`: FastAPI app with mounted local data volume.
- `frontend`: Next.js app configured to call the backend.

Persistent data lives under `data/` and is mounted into the backend container.

## Local File Layout

The runtime data directory is:

- `data/uploads/`: original Markdown/TXT uploads.
- `data/reports/`: generated Markdown interview reports.
- `data/memory/weakness_memory.md`: persistent weakness memory.
- `data/memory/interview_history.md`: persistent interview history.
- `data/memory/learning_profile.md`: persistent learning profile.
- `data/chroma/`: Chroma persistent vector store.
- `data/app.db`: SQLite database.

The repository implementation should later use:

- `frontend/`: Next.js application.
- `backend/`: FastAPI application.
- `docs/`: design specs, plans, and project documentation.
- `data/`: local runtime data, ignored or mounted as appropriate.

## Data Design

SQLite tables:

### `documents`

- `id`
- `collection`, default `default`
- `source_filename`
- `file_path`
- `file_type`, either `markdown` or `txt`
- `title`
- `summary`
- `tags`, JSON array
- `knowledge_points`, JSON array
- `weakness_candidates`, JSON array
- `analysis_status`, one of `pending`, `completed`, `failed`
- `index_status`, one of `pending`, `completed`, `failed`
- `created_at`
- `updated_at`

### `document_chunks`

- `id`
- `document_id`
- `chunk_index`
- `content_hash`
- `chroma_collection`
- `chroma_id`
- `created_at`

### `interview_configs`

- `id`
- `target_company`
- `target_role`
- `job_description`
- `extra_prompt`
- `mode`, one of `comprehensive`, `project_deep_dive`, `knowledge_drill`, `weakness_reinforcement`
- `chat_model_provider`
- `chat_model`
- `target_rounds`
- `is_last_used`
- `updated_at`

### `interview_sessions`

- `id`
- `config_id`
- `status`, one of `active`, `completed`, `cancelled`
- `current_round`
- `started_at`
- `ended_at`
- `report_path`

### `interview_turns`

- `id`
- `session_id`
- `round_index`
- `question`
- `answer`
- `feedback`
- `missing_points`, JSON array
- `follow_up_question`
- `follow_up_answer`
- `weaknesses`, JSON array
- `review_suggestions`, JSON array
- `retrieved_context_refs`, JSON array
- `created_at`

### `reports`

- `id`
- `session_id`
- `report_path`
- `summary`
- `weaknesses`, JSON array
- `created_at`

### `memory_files`

- `id`
- `kind`, one of `weakness`, `interview_history`, `learning_profile`
- `file_path`
- `summary_hash`
- `last_indexed_at`
- `updated_at`

Chroma metadata should include `source_type`, `document_id`, `memory_kind`, `session_id`, `chunk_index`, `collection`, `title`, and `tags`.

## API Draft

All APIs are unauthenticated local endpoints under `/api`.

- `GET /api/health`: return backend, SQLite, Chroma, and configured provider status.
- `GET /api/models`: return chat model options available from backend environment variables.
- `POST /api/documents/upload`: upload `.md` or `.txt`, analyze, persist, index, and return document status.
- `GET /api/documents`: list documents with optional keyword, tag, and status filters.
- `GET /api/documents/{document_id}`: return document metadata and index status.
- `PATCH /api/documents/{document_id}`: edit AI-generated metadata.
- `POST /api/documents/{document_id}/reindex`: rebuild Chroma chunks for the document.
- `GET /api/interview-configs/last`: return last-used or default interview config.
- `PUT /api/interview-configs/last`: save last-used interview config.
- `POST /api/interview-sessions`: create a session and generate the first question.
- `GET /api/interview-sessions/{session_id}`: return session, config, turns, and status.
- `POST /api/interview-sessions/{session_id}/answer`: submit main answer and receive feedback plus follow-up.
- `POST /api/interview-sessions/{session_id}/follow-up-answer`: submit follow-up answer.
- `POST /api/interview-sessions/{session_id}/next-question`: generate the next main question.
- `POST /api/interview-sessions/{session_id}/finish`: generate report, update memory files, and reindex report/memory.
- `GET /api/reports`: list generated reports.
- `GET /api/reports/{report_id}`: return Markdown report content.
- `GET /api/memory`: return memory file contents and update timestamps.
- `POST /api/rag/search`: local debug endpoint for inspecting retrieval results.

Error conventions:

- `400`: unsupported file type, invalid config, or invalid request state.
- `404`: document, session, or report not found.
- `409`: session state conflict, such as answering after finish.
- `502`: provider call failed.
- `503`: selected provider is not configured or local storage dependency is unavailable.

## Page Draft

### Dashboard

Route: `/`

Shows knowledge base status, indexed document count, recent weakness summary, latest interview report, and configured model availability. Primary actions are upload documents and start interview.

### Library

Route: `/library`

Supports Markdown/TXT upload, document list, analysis status, index status, tags, keyword filtering, and navigation to document details.

### Document Detail

Route: `/library/[documentId]`

Shows original filename, title, summary, tags, knowledge points, weakness candidates, and indexing state. User can edit metadata after automatic ingestion and trigger reindexing.

### Interview

Route: `/interview`

Loads the last-used configuration. User can set target company, role, JD, extra prompt, mode, model, and target rounds. The session area displays current question, answer box, feedback, missing points, follow-up question, next-question action, and finish action.

### Review & Memory

Route: `/review`

Shows report list, Markdown report preview, and read-only memory tabs for weakness memory, interview history, and learning profile. v1 does not provide manual memory editing in the UI.

## Memory File Format

The three memory files use fixed headings so `MemoryService` can update them predictably:

- `weakness_memory.md`: `# Weakness Memory`, `## Current Weakness Summary`, `## Weakness History`
- `interview_history.md`: `# Interview History`, `## Current Interview Summary`, `## Interview Records`
- `learning_profile.md`: `# Learning Profile`, `## Current Learning Profile`, `## Profile Updates`

After each completed interview, `MemoryService` rewrites only the current summary section for each file and appends a dated entry to that file's history section. Memory files are reindexed after updates so future interviews can retrieve them.

## Task Breakdown

Implementation should be planned in these phases:

1. Repository and infrastructure planning: monorepo structure, Docker Compose, environment variables, lint/test commands, and local data volume conventions.
2. Backend foundation: FastAPI app, config loading, Pydantic schemas, SQLite initialization, error format, health API, and model availability API.
3. Document upload and AI analysis: Markdown/TXT upload, original file storage, document analysis prompt, document metadata persistence, and document detail editing.
4. RAG indexing and retrieval: text splitting, OpenAI embedding, Chroma persistence, chunk metadata, reindexing, and debug search API.
5. Interview config and session state: last-used config, session creation, first-question generation, session and turn persistence.
6. Answer feedback and follow-up: answer submission, structured feedback generation, follow-up handling, weakness extraction, review suggestions, and next-question flow.
7. Report and long-term memory: Markdown report generation, memory file updates, report/memory reindexing, report API, and memory API.
8. Frontend pages: Dashboard, Library, Document Detail, Interview, Review & Memory, including loading, empty, and error states.

## Testing Strategy

Backend testing should come first because the backend owns workflows. Service tests should mock model providers. API tests should cover upload validation, document lifecycle, interview state transitions, report generation, and memory update behavior. RAG integration tests can use a temporary Chroma directory.

Frontend tests should focus on key interaction flows after API contracts are stable: upload result display, metadata editing, interview answer flow, and report/memory rendering.

Docker Compose verification should confirm a clean local startup, backend health, frontend access, persistent data volume behavior, and one manual end-to-end flow from upload to final report.

## Security and Configuration

API keys are read only from backend environment variables:

- `OPENAI_API_KEY`
- `DEEPSEEK_API_KEY`
- `QWEN_API_KEY`
- embedding model configuration, defaulting to `text-embedding-3-small`

The frontend never accepts API keys. SQLite, Chroma metadata, logs, reports, and memory files must not include provider secrets. Errors returned to the frontend should describe missing configuration without echoing sensitive values.

## Out of Scope for v1

- User login and permission system.
- Multi-user workspaces.
- Multi-agent collaboration.
- Frontend API key entry.
- Database API key storage.
- PDF and Word ingestion.
- Audio or video interviews.
- MySQL, Redis, Celery, or distributed workers.
- Complex scoring, ranking, or analytics dashboards.
- Custom interview mode template editor.

## Acceptance Criteria

- Docker Compose starts frontend and backend locally.
- Backend reports health and configured model availability.
- User can upload Markdown/TXT and receive AI-generated metadata.
- Uploaded document is saved, indexed in Chroma, and visible in the Library.
- User can edit document metadata and reindex the document.
- User can configure and start a mock interview using an available chat model.
- User can answer a main question and receive feedback, missing points, weakness extraction, review suggestions, and a follow-up question.
- User can proceed across target rounds or finish early.
- Finishing creates a Markdown report in `data/reports/`.
- Finishing updates `weakness_memory.md`, `interview_history.md`, and `learning_profile.md`.
- Updated report and memory content are retrievable in later RAG searches.
