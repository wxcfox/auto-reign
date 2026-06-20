# Auto Reign

*Autoregressive Q&A — every token is a step to the throne.*

**Local-first AI mock interviews grounded in your own knowledge base, with
automatic weakness tracking and review reports.**

Auto Reign ingests Markdown and TXT source documents, indexes their vectors in
Qdrant, runs written mock interviews, and stores interview history and review
memory locally.

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
```

Default host ports:

- `3100`: frontend
- `8300`: backend
- `13306`: MySQL
- `16333`: Qdrant HTTP

At least one backend provider key must be non-empty before that provider and its
models appear in the Interview selector. Keep `.env` local; it is ignored by Git.

## Runtime Modes

### Host-process development mode

`./start.sh` runs:

- MySQL in Docker for relational metadata
- Qdrant in Docker for vectors and retrieval
- FastAPI on the host
- Next.js on the host

Use this mode for day-to-day development. Runtime state lives in:

- MySQL: interview, document, chunk, report, and memory metadata
- Qdrant: indexed chunk vectors and retrieval payloads
- `DATA_DIR`: uploaded source files, generated reports, and local working data

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
| `MYSQL_DATABASE` | MySQL database name. |
| `MYSQL_USER` | MySQL application user. |
| `MYSQL_PASSWORD` | MySQL application password. |
| `MYSQL_ROOT_PASSWORD` | MySQL root password for local container setup. |
| `DATABASE_URL` | SQLAlchemy database URL used by the backend and Alembic. |
| `QDRANT_URL` | Backend-to-Qdrant URL. |
| `QDRANT_COLLECTION` | Default Qdrant collection name. |
| `DATA_DIR` | Root directory for uploads, generated reports, and local files. |
| `EMBEDDING_PROVIDER` | Embedding provider identifier. |
| `EMBEDDING_MODEL` | Embedding model identifier. |
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
endpoints. OpenAI embeddings require `OPENAI_API_KEY` even when chat uses DeepSeek or
Qwen. Set `DETERMINISTIC_MODEL_FALLBACK=true` only for automated tests or an explicitly
offline demo; this bypasses provider calls and uses stable local responses and hash
vectors.

## Document Library

Uploads support UTF-8 `.md` and `.txt` files. Original files are stored under
`DATA_DIR/uploads`; document and chunk metadata are stored in MySQL; chunk vectors are
stored in Qdrant. PDF, Word, image, audio, and video ingestion are not supported.

## Smoke Test

1. Run `./start.sh`.
2. Open Dashboard and confirm `Backend ready`.
3. Open Library and upload a Markdown file with a level-one heading.
4. Confirm analysis and indexing complete, then open the document detail page.
5. Configure at least one provider key in `.env` and restart with `./start.sh --restart`.
6. Open Interview, set company, role, mode, model, and target rounds.
7. Start a session, submit an answer and optional follow-up, then finish it.
8. Open Review and confirm the report and memory content are visible.

## V1 Exclusions

V1 has no authentication, authorization, multi-user isolation, background workers,
Redis, object storage, remote RAG runtime, binary document ingestion, voice/video
interviews, numeric scoring, or frontend API-key entry. It is intended for a single
user running the stack locally.
