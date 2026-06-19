# Auto Reign

Auto Reign is a local-first interview preparation workbench. It stores Markdown/TXT
source documents, indexes them in Chroma, runs written mock interviews, and persists
reports plus long-term review memory on the local machine.

## Docker Quick Start

Requirements: Docker with Compose v2.

```sh
cp .env.example .env
docker compose config
docker compose up --build
```

Open the frontend at <http://localhost:3000>. The backend health endpoint is
<http://localhost:8000/api/health>.

At least one backend provider key must be non-empty before that provider and its
models appear in the Interview selector. Keep `.env` local; it is ignored by Git.
Stop the stack with `docker compose down`. Persistent application data remains in
`./data`.

## Local Development

The backend requires Python 3.12+ and `uv`:

```sh
cd backend
uv sync
set -a; source ../.env; set +a
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The frontend requires Node.js 22+:

```sh
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Run the canonical checks before committing:

```sh
cd backend
uv run pytest -v
uv run ruff check .

cd ../frontend
npm test
npm run build
```

## Configuration

| Variable | Purpose |
| --- | --- |
| `BACKEND_HOST` | Backend bind host inside the container. |
| `BACKEND_PORT` | Host port mapped to backend port `8000`. |
| `FRONTEND_PORT` | Host port mapped to frontend port `3000`. |
| `DATA_DIR` | Root directory for local uploads, reports, and memory. |
| `SQLITE_PATH` | SQLite database path. |
| `CHROMA_DIR` | Chroma persistence directory. |
| `DEFAULT_COLLECTION` | Default Chroma collection name. |
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
| `DETERMINISTIC_MODEL_FALLBACK` | Uses local deterministic chat and vectors for tests/offline demos. |
| `NEXT_PUBLIC_API_BASE_URL` | Public browser URL for the backend API. |

Provider keys are read only from the backend environment. The API returns boolean
availability and configured model names, never key values. Keys are not accepted by
the frontend and are not written to SQLite, Chroma, reports, or memory files.

OpenAI uses the standard API endpoint. DeepSeek and Qwen use their OpenAI-compatible
endpoints. OpenAI embeddings require `OPENAI_API_KEY` even when chat uses DeepSeek or
Qwen. Set `DETERMINISTIC_MODEL_FALLBACK=true` only for automated tests or an explicitly
offline demo; this bypasses all provider calls and uses stable local responses and
hash vectors.

## Document Library

Uploads support UTF-8 `.md` and `.txt` files. Original files are stored under
`DATA_DIR/uploads`; document metadata is stored in SQLite; chunks and vectors are
stored in Chroma. PDF, Word, image, audio, and video ingestion are not supported.

## Smoke Test

1. Open Dashboard and confirm `Backend ready`.
2. Open Library and upload a Markdown file with a level-one heading.
3. Confirm analysis and indexing complete, then open the document detail page.
4. Configure at least one provider key in `.env` and restart the backend.
5. Open Interview, set company, role, mode, model, and target rounds.
6. Start a session, submit an answer and optional follow-up, then finish it.
7. Open Review and confirm the report and memory content are visible.

## V1 Exclusions

V1 has no authentication, authorization, multi-user isolation, background workers,
Redis, MySQL, cloud deployment configuration, binary document ingestion, voice/video
interviews, numeric scoring, or frontend API-key entry. It is intended for a single
user running the stack locally.
