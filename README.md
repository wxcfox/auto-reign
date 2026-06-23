# Auto Reign

*Autoregressive Q&A — every token is a step to the throne.*

**Local-first AI mock interviews grounded in your own knowledge base, with
automatic weakness tracking and review reports.**

## Development Direction

This README documents the currently runnable v1 implementation. The canonical
design for the next development cycle is the
[filesystem-first interview workbench specification](docs/superpowers/specs/2026-06-22-filesystem-first-interview-workbench-design.md).

That redesign makes user-visible Markdown files the durable learning assets,
automates document organization and learning-state maintenance, and treats
MySQL and Qdrant as operational or rebuildable infrastructure. It intentionally
does not preserve existing runtime data formats. Contributors implementing the
redesign must first create a phased plan from the specification and keep the
application runnable at each phase boundary.

Auto Reign ingests Markdown, TXT, PDF, DOCX, and free-form learning notes into a
local workspace, indexes searchable chunks in Qdrant, runs written mock
interviews in a chat-style flow, and stores interview history and review memory
locally.

## Development Quick Start

Requirements:

- Docker with Compose v2
- Python 3.12+
- `uv`
- Node.js 22+
- `npm`

Start the local development stack with the repository entrypoint:

```sh
./start.sh
```

The script copies `.env.example` to `.env` when needed, starts MySQL and Qdrant in
Docker, runs Alembic migrations, installs frontend dependencies when missing, and
starts the backend and frontend as host processes.

Lifecycle commands:

```sh
./start.sh --status
./start.sh --stop
./start.sh --restart
./start.sh --help
./reset-data.sh --dry-run
./reset-data.sh --yes
```

`./reset-data.sh --yes` is destructive: it stops the local Auto Reign processes,
removes the MySQL and Qdrant Docker volumes, and deletes local runtime data such
as `data/`, `.pids/`, and `logs/`. It keeps source code, dependencies, and local
configuration files such as `.env`.

Default host ports:

- `3100`: frontend
- `8300`: backend
- `13306`: MySQL
- `16333`: Qdrant HTTP

Default dependency images:

- `MYSQL_IMAGE=mysql:8.4`
- `QDRANT_IMAGE=qdrant/qdrant:v1.17.0`

At least one backend provider key must be non-empty before that provider and its
models appear in the Interview selector. The default local path is tuned for a
single `QWEN_API_KEY`: chat uses `qwen3.7-plus` by default and RAG embeddings use `text-embedding-v4`
through the DashScope OpenAI-compatible endpoint. `QWEN_CHAT_MODELS` only controls
the Interview model picker for chat models; embeddings stay separate under
`EMBEDDING_PROVIDER` and `EMBEDDING_MODEL`. Keep `.env` local; it is ignored by Git.
If Docker Hub access is unstable in your environment, override `MYSQL_IMAGE` and
`QDRANT_IMAGE` in `.env` with reachable mirror image references before rerunning
`./start.sh`.

## Runtime Modes

### Host-process development mode

`./start.sh` runs:

- MySQL in Docker for relational metadata
- Qdrant in Docker for vectors and retrieval
- FastAPI on the host
- Next.js on the host

Use this mode for day-to-day development. Runtime state lives in:

- MySQL: workspace artifact projection, interview, report, and memory metadata
- Qdrant: indexed chunk vectors and retrieval payloads
- `DATA_DIR`: workspace source files, extracted text, generated Markdown, reports,
  revisions, and local working data

### Full-container mode

Use Compose directly when you want all four services inside Docker:

```sh
cp .env.example .env
docker compose config
docker compose up --build -d
```

Open the frontend at <http://127.0.0.1:3100>. The backend health endpoint is
<http://127.0.0.1:8300/api/health>.

Stop the stack with:

```sh
docker compose down
```

Persistent data remains in the named MySQL and Qdrant volumes plus `./data`.

## Canonical Checks

Run the repository checks before committing:

```sh
cd backend
uv run pytest -v
uv run ruff check .

cd ../frontend
npm test
npm run build

cd ..
docker compose config
```

## Configuration

| Variable | Purpose |
| --- | --- |
| `BACKEND_HOST` | Backend bind host for container mode. |
| `BACKEND_PORT` | Preferred host port for the backend process or container mapping. |
| `FRONTEND_PORT` | Preferred host port for the frontend process or container mapping. |
| `MYSQL_PORT` | Host port mapped to MySQL container port `3306`. |
| `QDRANT_HTTP_PORT` | Host port mapped to Qdrant HTTP port `6333`. |
| `QDRANT_GRPC_PORT` | Host port mapped to Qdrant gRPC port `6334`. |
| `MYSQL_IMAGE` | Container image reference for the local MySQL dependency. |
| `QDRANT_IMAGE` | Container image reference for the local Qdrant dependency. |
| `MYSQL_DATABASE` | MySQL database name. |
| `MYSQL_USER` | MySQL application user. |
| `MYSQL_PASSWORD` | MySQL application password. |
| `MYSQL_ROOT_PASSWORD` | MySQL root password for local container setup. |
| `DATABASE_URL` | SQLAlchemy database URL used by the backend and Alembic. |
| `QDRANT_URL` | Backend-to-Qdrant URL. |
| `QDRANT_COLLECTION` | Default Qdrant collection name. |
| `DATA_DIR` | Root directory for workspace sources, extracted text, generated reports, revisions, and local files. |
| `EMBEDDING_PROVIDER` | Embedding provider identifier. Defaults to `qwen`. |
| `EMBEDDING_MODEL` | Embedding model identifier. Defaults to `text-embedding-v4`. |
| `OPENAI_API_KEY` | Enables the OpenAI model catalog. Backend only. |
| `DEEPSEEK_API_KEY` | Enables the DeepSeek model catalog. Backend only. |
| `QWEN_API_KEY` | Enables the Qwen model catalog. Backend only. |
| `OPENAI_CHAT_MODELS` | Comma-separated OpenAI model allowlist. |
| `DEEPSEEK_CHAT_MODELS` | Comma-separated DeepSeek model allowlist. |
| `QWEN_CHAT_MODELS` | Comma-separated Qwen model allowlist. |
| `DEEPSEEK_BASE_URL` | DeepSeek OpenAI-compatible API base URL. |
| `QWEN_BASE_URL` | Qwen OpenAI-compatible regional API base URL. |
| `DETERMINISTIC_MODEL_FALLBACK` | Uses local deterministic chat and vectors for tests or offline demos. |
| `NEXT_PUBLIC_API_BASE_URL` | Public browser URL for the backend API. |

Provider keys are read only from the backend environment. The API returns boolean
availability and configured model names, never key values. Keys are not accepted by
the frontend and are not written to MySQL, Qdrant, reports, or memory files.

OpenAI uses the standard API endpoint. DeepSeek and Qwen use their OpenAI-compatible
endpoints. The default setup uses Qwen for both chat and embeddings, so a valid
`QWEN_API_KEY` is enough to run document indexing, retrieval, and interviews locally.
OpenAI remains supported for both chat and embeddings when `EMBEDDING_PROVIDER=openai`
and `EMBEDDING_MODEL=text-embedding-3-small`. Set `DETERMINISTIC_MODEL_FALLBACK=true`
only for automated tests or an explicitly offline demo; this bypasses provider calls
and uses stable local responses and hash vectors.

## Document Library

Uploads support `.md`, `.txt`, `.pdf`, and `.docx` files. Original files are
stored under `DATA_DIR/sources/documents`; extracted PDF/DOCX text is stored under
`DATA_DIR/sources/extracted` when available. The workspace projection is stored in
MySQL, and indexable source, extracted, knowledge, and practice content is chunked
and stored as vectors in Qdrant. See
[Knowledge Base Data Flow](docs/knowledge-data-flow.md) for the current data path.

## Smoke Test

1. Run `./start.sh`.
2. Open Workbench and confirm the local stack is reachable.
3. Open Library and upload a Markdown, TXT, PDF, or DOCX file.
4. Confirm the file appears with its original filename, category, timestamps, and
   edit/delete actions.
5. Configure `QWEN_API_KEY` in `.env` and restart with `./start.sh --restart`.
6. Open New interview, optionally describe the company, role, or JD in the chat
   composer, then start answering in the conversation flow.
7. Continue until the configured interview completes and the summary appears in
   the chat.
8. Open History from the sidebar and confirm completed interviews are visible but
   cannot be resumed.

## V1 Exclusions

V1 has no authentication, authorization, multi-user isolation, background workers,
Redis, object storage, remote RAG runtime, scanned-image OCR, voice/video
interviews, numeric scoring, or frontend API-key entry. It is intended for a
single user running the stack locally.
