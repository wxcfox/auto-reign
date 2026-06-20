# Auto Reign MySQL, Qdrant, and Startup Design

## Context

Auto Reign currently uses SQLite for relational data, embedded Chroma for vector
data, and separate manual commands for the backend and frontend. The next
development step targets multi-user and remote deployment while preserving a
simple local workflow similar to Wegent.

This design replaces the production SQLite and Chroma paths with MySQL and
Qdrant, adds a repository-level startup script, and keeps RAG execution inside
the backend. Existing local SQLite and Chroma data will not be migrated.

## Goals

- Start local development with `./start.sh`.
- Support `./start.sh --status`, `./start.sh --stop`, and
  `./start.sh --restart`.
- Run the backend and frontend as host processes during development.
- Run MySQL and Qdrant as Docker dependency containers during development.
- Retain a full-container `docker compose up --build` deployment mode.
- Avoid default port conflicts with Wegent.
- Store relational metadata in MySQL and vectors in Qdrant.
- Keep the current document, memory, interview, and RAG behavior intact.

## Non-Goals

- Migrating existing SQLite or Chroma development data.
- Adding Redis, background workers, or distributed locks.
- Adding object storage for uploads, reports, or memory files.
- Creating a separate `knowledge_engine` package or `knowledge_runtime` service.
- Supporting local/remote RAG runtime switching.
- Supporting multiple vector database providers in this change.

## Runtime Architecture

### Development Mode

`./start.sh` performs the following sequence:

1. Load `.env`, creating it from `.env.example` when absent.
2. Validate Docker, Docker Compose, `uv`, Node.js, and npm availability.
3. Resolve available application ports, starting from the configured defaults.
4. Start the MySQL and Qdrant Compose services.
5. Wait for both dependency health checks.
6. Apply Alembic migrations to MySQL.
7. Start the backend and frontend as background host processes.
8. Wait for application health checks and report URLs and log locations.

The script exits after successful startup while the application processes keep
running. Runtime state is stored under `.pids/`, and output is stored under
`logs/`. Both directories are ignored by Git.

Commands are idempotent:

- `./start.sh` starts missing services and leaves healthy services running.
- `./start.sh --status` reports PID, actual port, and health for each component.
- `./start.sh --stop` stops host processes and this project's MySQL and Qdrant
  containers without deleting volumes.
- `./start.sh --restart` performs a stop followed by a normal start.

If application startup fails, the script stops host processes started by the
current invocation and prints the relevant log path. It does not delete data or
stop dependency containers that were already running before the invocation.

### Full-Container Mode

`docker compose up --build` starts these services:

- `mysql`
- `qdrant`
- `backend`
- `frontend`

The backend waits for healthy MySQL and Qdrant services and applies database
migrations before serving requests. Named volumes persist MySQL and Qdrant
data. Existing bind-mounted application data continues to persist uploaded
files, reports, and Markdown memory files.

Compose uses a stable project and service naming scheme so all Auto Reign
containers are grouped clearly in Docker Desktop.

## Ports

Defaults avoid Wegent's standard ports:

| Component | Host default | Container port |
| --- | ---: | ---: |
| Frontend | 3100 | 3000 |
| Backend | 8300 | 8000 |
| MySQL | 13306 | 3306 |
| Qdrant HTTP | 16333 | 6333 |
| Qdrant gRPC | 16334 | 6334 |

For development mode, occupied frontend or backend ports advance to the next
available port. The script passes the selected backend URL to the frontend and
records selected ports in `.pids/`. Dependency ports are configurable through
`.env`; an occupied configured MySQL or Qdrant port is treated as a startup
error because silently changing externally persisted dependency endpoints can
connect the application to the wrong service.

## Configuration

The primary storage settings are:

- `DATABASE_URL`: SQLAlchemy MySQL URL.
- `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, and
  `MYSQL_ROOT_PASSWORD`: Compose initialization values.
- `QDRANT_URL`: backend-to-Qdrant URL.
- `QDRANT_COLLECTION`: defaults to `auto_reign_default`.
- `DATA_DIR`: upload, report, and memory file root.

`SQLITE_PATH` and `CHROMA_DIR` are removed from runtime configuration. Secrets
are present only in `.env` or process environments and are not written to logs,
PID files, or tracked configuration.

## Relational Data and Schema Management

MySQL stores documents, document chunk metadata, interview configurations,
interview sessions and turns, report records, memory file records, and related
status fields. Uploaded source files, generated reports, and Markdown memory
content remain under `DATA_DIR` in this change.

Alembic becomes the schema authority. An initial migration creates the current
schema using MySQL-compatible column definitions. Runtime startup runs
`alembic upgrade head`; production startup no longer calls
`Base.metadata.create_all()`.

`DocumentChunk` storage-neutral names replace Chroma-specific fields:

- `chroma_collection` becomes `vector_collection`.
- `chroma_id` becomes `vector_id`.

Because existing development data is explicitly disposable, no data-copy or
compatibility migration is provided. Unit tests may continue using temporary
SQLite databases for speed, but MySQL is the default application database and
is covered by integration smoke testing.

## RAG Architecture

RAG remains an in-process backend capability:

```text
DocumentService / MemoryService
        -> RagService
        -> Embedding provider
        -> VectorStore protocol
        -> QdrantVectorStore
```

The `VectorStore` protocol contains only the operations currently required by
the application: upsert chunks, delete a document's chunks, and similarity
search. It isolates Qdrant client details and permits deterministic unit tests;
it is not a multi-provider configuration system.

Qdrant uses one configured collection. Point payloads include:

- chunk content
- `source_type`
- `source_id`
- `document_id` when applicable
- `chunk_index`
- collection name
- title and tags where available

The collection is created lazily from the first indexed embedding's dimension
and uses cosine distance. An existing collection with a different vector
dimension produces an explicit configuration error. The application does not
silently recreate collections or delete vectors.

Document reindexing deletes existing points filtered by `document_id`, removes
old relational chunk metadata, then writes new vectors and metadata. Successful
completion sets `Document.index_status` to `completed`. An indexing failure sets
the status to `failed`, preserves the document record and source file, and
allows the existing reindex endpoint to retry. Upload and reindex endpoints
return the document with `index_status=failed` instead of raising after the
failure status is recorded; this allows the request transaction to commit and
makes the retry state observable through the existing document response.

Search embeds the query, executes Qdrant similarity search, and maps results to
the existing API response shape. Qdrant unavailability maps to a service
unavailable response instead of an empty successful result.

No `knowledge_runtime`, remote client, or runtime mode is implemented. A
separate runtime can be designed later only if independent RAG deployment or
scaling becomes an observed requirement.

## Failure Handling

- Missing development prerequisites fail before services are changed.
- MySQL connection or migration failure prevents backend startup; there is no
  SQLite fallback.
- Qdrant must pass health checks before the backend starts.
- Qdrant connection failures and embedding provider failures retain distinct
  domain error codes.
- Embedding dimension conflicts are explicit configuration errors.
- Start and stop operations validate recorded PIDs before signalling processes,
  preventing stale PID files from stopping unrelated processes.
- Stop and failed-start cleanup never delete Docker volumes.

## Testing and Verification

Automated coverage includes:

- settings and SQLAlchemy engine construction for MySQL
- Alembic schema creation against MySQL
- Qdrant collection creation, point upsert, document deletion, search mapping,
  dimension mismatch, and connection failure behavior
- RAG and document API regressions using test doubles at the `VectorStore`
  boundary
- indexing failure status and retry behavior
- startup argument parsing, PID validation, idempotent start, stop, restart, and
  status behavior
- existing backend test suite
- existing frontend tests and production build
- `docker compose config` validation

End-to-end smoke verification runs real MySQL and Qdrant containers and checks:

1. `./start.sh` reaches healthy frontend, backend, MySQL, and Qdrant states.
2. `./start.sh --status` reports the running services and actual ports.
3. A document can be uploaded, indexed, and retrieved through RAG.
4. `./start.sh --restart` restores all services.
5. `./start.sh --stop` stops services without deleting persisted data.
6. Full-container Compose mode becomes healthy on the non-conflicting ports.

## Acceptance Criteria

- A new checkout with Docker, uv, Node.js, and npm can start development using
  `./start.sh` after configuring required model credentials.
- Development and full-container modes use MySQL and Qdrant, not SQLite or
  Chroma.
- Wegent and Auto Reign can run concurrently on their documented default ports.
- Startup lifecycle commands are documented in the repository README.
- Docker Desktop clearly shows the Auto Reign dependency and application
  containers.
- All automated tests, builds, Compose validation, and lifecycle smoke tests
  pass.
