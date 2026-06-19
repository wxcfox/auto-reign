# Auto Reign V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v1 local single-user AI mock interview and knowledge memory system described in `docs/superpowers/specs/2026-06-19-auto-reign-v1-design.md`.

**Architecture:** Use a monorepo with `backend/` for FastAPI domain services and `frontend/` for a Next.js App Router UI. Backend owns document ingestion, RAG indexing, interview orchestration, report generation, and memory updates; frontend calls backend APIs and never handles provider secrets.

**Tech Stack:** Next.js, React, TypeScript, FastAPI, Pydantic, SQLAlchemy, SQLite, LangChain, Chroma, OpenAI-compatible chat providers, Docker Compose, pytest, Vitest.

---

## File Structure

Create or modify these files across the implementation:

- Create: `.gitignore`
- Create: `.env.example`
- Create: `docker-compose.yml`
- Create: `README.md`
- Create: `backend/Dockerfile`
- Create: `backend/pyproject.toml`
- Create: `backend/app/main.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/health.py`
- Create: `backend/app/api/models.py`
- Create: `backend/app/api/documents.py`
- Create: `backend/app/api/interviews.py`
- Create: `backend/app/api/reports.py`
- Create: `backend/app/api/memory.py`
- Create: `backend/app/api/rag.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/errors.py`
- Create: `backend/app/db/models.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/repositories/sqlite.py`
- Create: `backend/app/repositories/chroma_store.py`
- Create: `backend/app/schemas/common.py`
- Create: `backend/app/schemas/documents.py`
- Create: `backend/app/schemas/interviews.py`
- Create: `backend/app/schemas/reports.py`
- Create: `backend/app/schemas/memory.py`
- Create: `backend/app/services/model_service.py`
- Create: `backend/app/services/document_service.py`
- Create: `backend/app/services/rag_service.py`
- Create: `backend/app/services/interview_service.py`
- Create: `backend/app/services/memory_service.py`
- Create: `backend/app/services/config_service.py`
- Create: `backend/app/prompts/document_analysis.md`
- Create: `backend/app/prompts/question_generation.md`
- Create: `backend/app/prompts/answer_feedback.md`
- Create: `backend/app/prompts/report_generation.md`
- Create: `backend/app/prompts/memory_update.md`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health_and_models.py`
- Create: `backend/tests/test_documents.py`
- Create: `backend/tests/test_rag.py`
- Create: `backend/tests/test_interviews.py`
- Create: `backend/tests/test_memory.py`
- Create: `frontend/Dockerfile`
- Create: `frontend/package.json`
- Create: `frontend/next.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/app/layout.tsx`
- Create: `frontend/src/app/page.tsx`
- Create: `frontend/src/app/library/page.tsx`
- Create: `frontend/src/app/library/[documentId]/page.tsx`
- Create: `frontend/src/app/interview/page.tsx`
- Create: `frontend/src/app/review/page.tsx`
- Create: `frontend/src/app/globals.css`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/types.ts`
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/components/StatusPill.tsx`
- Create: `frontend/src/components/MarkdownView.tsx`
- Create: `frontend/src/components/DocumentUploader.tsx`
- Create: `frontend/src/components/InterviewWorkspace.tsx`
- Create: `frontend/src/components/__tests__/DocumentUploader.test.tsx`
- Create: `frontend/src/components/__tests__/InterviewWorkspace.test.tsx`

## Shared Implementation Rules

- Run backend tests from `backend/` with `pytest`.
- Run frontend tests from `frontend/` with `npm test`.
- Use fake model providers in tests; do not call real OpenAI, DeepSeek, or Qwen APIs in automated tests.
- Do not log API keys or return API keys from any endpoint.
- Keep runtime data under `data/`, mounted into the backend container.
- Commit after each task when its verification passes.

### Task 1: Repository Infrastructure

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `docker-compose.yml`
- Create: `README.md`

- [ ] **Step 1: Add ignore rules**

Create `.gitignore` with:

```gitignore
.DS_Store
.env
.env.local
.superpowers/
data/
backend/.venv/
backend/.pytest_cache/
backend/__pycache__/
backend/**/*.pyc
frontend/node_modules/
frontend/.next/
frontend/coverage/
frontend/.turbo/
```

- [ ] **Step 2: Add environment example**

Create `.env.example` with:

```dotenv
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
FRONTEND_PORT=3000
DATA_DIR=/app/data
SQLITE_PATH=/app/data/app.db
CHROMA_DIR=/app/data/chroma
DEFAULT_COLLECTION=default
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
QWEN_API_KEY=
OPENAI_CHAT_MODELS=gpt-4.1-mini,gpt-4.1
DEEPSEEK_CHAT_MODELS=deepseek-chat
QWEN_CHAT_MODELS=qwen-plus,qwen-max
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

- [ ] **Step 3: Add Docker Compose**

Create `docker-compose.yml` with two services and local volumes:

```yaml
services:
  backend:
    build:
      context: ./backend
    env_file:
      - .env
    ports:
      - "${BACKEND_PORT:-8000}:8000"
    volumes:
      - ./data:/app/data

  frontend:
    build:
      context: ./frontend
    environment:
      NEXT_PUBLIC_API_BASE_URL: ${NEXT_PUBLIC_API_BASE_URL:-http://localhost:8000}
    ports:
      - "${FRONTEND_PORT:-3000}:3000"
    depends_on:
      - backend
```

- [ ] **Step 4: Add README quick start**

Create `README.md` with local setup, environment variables, and the v1 scope. Include these commands:

```bash
cp .env.example .env
docker compose config
docker compose up --build
```

- [ ] **Step 5: Verify Compose syntax**

Run:

```bash
docker compose --env-file .env.example config
```

Expected: command exits 0 and prints normalized `backend` and `frontend` services.

- [ ] **Step 6: Commit**

```bash
git add .gitignore .env.example docker-compose.yml README.md
git commit -m "chore: add repository infrastructure"
```

### Task 2: Backend Skeleton, Configuration, and Health API

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/Dockerfile`
- Create: `backend/app/main.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/health.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/core/errors.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health_and_models.py`

- [ ] **Step 1: Write failing health test**

Create `backend/tests/test_health_and_models.py` with:

```python
from fastapi.testclient import TestClient


def test_health_reports_local_dependencies(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["storage"]["sqlite"] == "configured"
    assert body["storage"]["chroma"] == "configured"
    assert "providers" in body
```

- [ ] **Step 2: Add pytest fixtures**

Create `backend/tests/conftest.py` with a temporary data directory and TestClient:

```python
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
```

- [ ] **Step 3: Run test to verify it fails**

Run:

```bash
cd backend && pytest tests/test_health_and_models.py::test_health_reports_local_dependencies -v
```

Expected: FAIL with an import error because `app.main` does not exist.

- [ ] **Step 4: Add backend package metadata**

Create `backend/pyproject.toml` with Python 3.12, FastAPI, Pydantic settings, SQLAlchemy, Chroma, LangChain, OpenAI SDK, pytest, and Ruff dependencies. Configure pytest path to `tests`.

- [ ] **Step 5: Implement settings**

Create `backend/app/core/config.py` with:

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    sqlite_path: Path = Path("data/app.db")
    chroma_dir: Path = Path("data/chroma")
    default_collection: str = "default"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    qwen_api_key: str | None = None
    openai_chat_models: str = "gpt-4.1-mini,gpt-4.1"
    deepseek_chat_models: str = "deepseek-chat"
    qwen_chat_models: str = "qwen-plus,qwen-max"

    def ensure_data_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "reports").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "memory").mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_data_dirs()
    return settings
```

- [ ] **Step 6: Implement health route**

Create `backend/app/api/health.py` with `router = APIRouter(prefix="/api")` and a `GET /health` endpoint returning:

```python
{
    "status": "ok",
    "storage": {"sqlite": "configured", "chroma": "configured"},
    "providers": {"openai": bool(settings.openai_api_key), "deepseek": bool(settings.deepseek_api_key), "qwen": bool(settings.qwen_api_key)},
}
```

- [ ] **Step 7: Implement app factory**

Create `backend/app/main.py` with `create_app() -> FastAPI`, include the health router, and expose `app = create_app()`.

- [ ] **Step 8: Add backend Dockerfile**

Create `backend/Dockerfile` that installs project dependencies and runs:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 9: Run health test to verify it passes**

Run:

```bash
cd backend && pytest tests/test_health_and_models.py::test_health_reports_local_dependencies -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add backend
git commit -m "feat: add backend health API"
```

### Task 3: Model Availability API and Secret-Safe Config Service

**Files:**
- Create: `backend/app/api/models.py`
- Create: `backend/app/services/config_service.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_health_and_models.py`

- [ ] **Step 1: Add failing model availability tests**

Append to `backend/tests/test_health_and_models.py`:

```python
def test_models_only_returns_configured_providers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as configured_client:
        response = configured_client.get("/api/models")
    assert response.status_code == 200
    body = response.json()
    assert body["providers"] == [
        {"provider": "openai", "models": ["gpt-4.1-mini", "gpt-4.1"]}
    ]
    assert "sk-test" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && pytest tests/test_health_and_models.py::test_models_only_returns_configured_providers -v
```

Expected: FAIL with 404 for `/api/models`.

- [ ] **Step 3: Implement config service**

Create `backend/app/services/config_service.py` with `available_chat_models(settings)` that returns only providers with non-empty API keys and splits comma-separated model strings into trimmed arrays.

- [ ] **Step 4: Implement models route**

Create `backend/app/api/models.py` with `GET /api/models` returning:

```python
{"providers": available_chat_models(settings)}
```

Include the router in `backend/app/main.py`.

- [ ] **Step 5: Run tests**

Run:

```bash
cd backend && pytest tests/test_health_and_models.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/models.py backend/app/services/config_service.py backend/app/main.py backend/tests/test_health_and_models.py
git commit -m "feat: expose configured chat models"
```

### Task 4: SQLite Schema and Repository Layer

**Files:**
- Create: `backend/app/db/models.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/repositories/sqlite.py`
- Create: `backend/app/schemas/common.py`
- Create: `backend/app/schemas/documents.py`
- Create: `backend/app/schemas/interviews.py`
- Create: `backend/app/schemas/reports.py`
- Create: `backend/app/schemas/memory.py`
- Create: `backend/tests/test_schema.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing schema test**

Create `backend/tests/test_schema.py`:

```python
from sqlalchemy import inspect

from app.core.config import get_settings
from app.db.session import create_engine_for_settings, init_db


def test_init_db_creates_required_tables(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_engine_for_settings(settings)
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {
        "documents",
        "document_chunks",
        "interview_configs",
        "interview_sessions",
        "interview_turns",
        "reports",
        "memory_files",
    }.issubset(tables)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && pytest tests/test_schema.py -v
```

Expected: FAIL because `app.db.session` does not exist.

- [ ] **Step 3: Implement SQLAlchemy models**

Create `backend/app/db/models.py` using SQLAlchemy 2.0 declarative models for the seven tables in the design spec. Use JSON columns for array fields and UTC timestamp defaults for `created_at` and `updated_at`.

- [ ] **Step 4: Implement database session helpers**

Create `backend/app/db/session.py` with:

```python
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import Base


def create_engine_for_settings(settings: Settings) -> Engine:
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{settings.sqlite_path}", connect_args={"check_same_thread": False})


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 5: Implement repository wrappers**

Create `backend/app/repositories/sqlite.py` with small repository classes for documents, chunks, configs, sessions, turns, reports, and memory files. Each method accepts an explicit `Session`.

- [ ] **Step 6: Implement Pydantic schemas**

Create schema modules with request and response models matching the API draft. Use literal types for statuses and modes.

- [ ] **Step 7: Initialize DB at startup**

Modify `backend/app/main.py` to create the engine, call `init_db`, and attach `app.state.session_factory`.

- [ ] **Step 8: Run schema tests**

Run:

```bash
cd backend && pytest tests/test_schema.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/db backend/app/repositories backend/app/schemas backend/app/main.py backend/tests/test_schema.py
git commit -m "feat: add sqlite schema"
```

### Task 5: Document Upload and AI Metadata Analysis

**Files:**
- Create: `backend/app/prompts/document_analysis.md`
- Create: `backend/app/services/model_service.py`
- Create: `backend/app/services/document_service.py`
- Create: `backend/app/api/documents.py`
- Create: `backend/tests/test_documents.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing document upload test**

Create `backend/tests/test_documents.py`:

```python
from fastapi.testclient import TestClient


def test_upload_markdown_analyzes_and_persists_document(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("resume.md", b"# Resume\n\nBuilt RAG systems with FastAPI.", "text/markdown")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_filename"] == "resume.md"
    assert body["file_type"] == "markdown"
    assert body["title"]
    assert body["summary"]
    assert body["analysis_status"] == "completed"
    assert body["index_status"] in {"pending", "completed"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && pytest tests/test_documents.py::test_upload_markdown_analyzes_and_persists_document -v
```

Expected: FAIL with 404 for `/api/documents/upload`.

- [ ] **Step 3: Add document analysis prompt**

Create `backend/app/prompts/document_analysis.md` instructing the model to return strict JSON:

```json
{
  "title": "string",
  "summary": "string",
  "tags": ["string"],
  "knowledge_points": ["string"],
  "weakness_candidates": ["string"]
}
```

- [ ] **Step 4: Implement model service with deterministic test fallback**

Create `backend/app/services/model_service.py` with:

- `ModelService.analyze_document(text: str) -> DocumentAnalysisResult`
- `ModelService.generate_question(request: QuestionGenerationRequest) -> str`
- `ModelService.evaluate_answer(request: AnswerEvaluationRequest) -> AnswerEvaluationResult`
- `ModelService.generate_report(request: ReportGenerationRequest) -> str`
- `ModelService.update_memory(request: MemoryUpdateRequest) -> MemoryUpdateResult`
- A fake deterministic branch used when no provider key is configured in tests.

The fake document analysis should derive a title from the first Markdown heading or filename fallback, return a one-sentence summary, and produce tags from frequent words.

- [ ] **Step 5: Implement document service**

Create `backend/app/services/document_service.py` with `upload_document(session, upload_file)` that:

- accepts `.md` and `.txt`;
- rejects other extensions with HTTP 400;
- stores original bytes under `data/uploads/<document-id>-<safe-filename>`;
- calls `ModelService.analyze_document`;
- writes a `documents` row with `analysis_status="completed"` and `index_status="pending"`;
- returns a Pydantic document response.

- [ ] **Step 6: Implement documents API**

Create `backend/app/api/documents.py` with:

- `POST /api/documents/upload`
- `GET /api/documents`
- `GET /api/documents/{document_id}`

Include the router in `backend/app/main.py`.

- [ ] **Step 7: Add unsupported file test**

Append:

```python
def test_upload_rejects_pdf(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("resume.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_file_type"
```

- [ ] **Step 8: Run document tests**

Run:

```bash
cd backend && pytest tests/test_documents.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/prompts/document_analysis.md backend/app/services/model_service.py backend/app/services/document_service.py backend/app/api/documents.py backend/app/main.py backend/tests/test_documents.py
git commit -m "feat: add document upload analysis"
```

### Task 6: RAG Indexing, Reindexing, and Debug Search

**Files:**
- Create: `backend/app/repositories/chroma_store.py`
- Create: `backend/app/services/rag_service.py`
- Create: `backend/app/api/rag.py`
- Create: `backend/tests/test_rag.py`
- Modify: `backend/app/api/documents.py`
- Modify: `backend/app/services/document_service.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing RAG test**

Create `backend/tests/test_rag.py`:

```python
from fastapi.testclient import TestClient


def test_uploaded_document_is_searchable(client: TestClient) -> None:
    upload = client.post(
        "/api/documents/upload",
        files={"file": ("notes.txt", b"FastAPI dependency injection and Chroma retrieval notes.", "text/plain")},
    )
    assert upload.status_code == 200
    search = client.post("/api/rag/search", json={"query": "Chroma retrieval", "limit": 3})
    assert search.status_code == 200
    hits = search.json()["hits"]
    assert hits
    assert hits[0]["source_type"] == "document"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && pytest tests/test_rag.py::test_uploaded_document_is_searchable -v
```

Expected: FAIL because `/api/rag/search` does not exist or upload leaves `index_status="pending"`.

- [ ] **Step 3: Implement Chroma repository**

Create `backend/app/repositories/chroma_store.py` with methods:

- `upsert_chunks(collection_name, chunks)`
- `delete_document_chunks(collection_name, document_id)`
- `search(collection_name, query_embedding, limit)`

Use persistent Chroma client pointed at `settings.chroma_dir`.

- [ ] **Step 4: Implement RAG service**

Create `backend/app/services/rag_service.py` with:

- `split_text(text, chunk_size=900, overlap=120)`
- `embed_texts(texts)` using OpenAI embeddings when configured and deterministic hash-based vectors in tests.
- `index_document(session, document)`
- `reindex_document(session, document_id)`
- `search(session, query, limit)`

Store Chroma ids in `document_chunks` and set document `index_status="completed"` after successful indexing.

- [ ] **Step 5: Wire indexing into upload**

Modify `DocumentService.upload_document` so upload calls `RagService.index_document` synchronously after metadata persistence.

- [ ] **Step 6: Add reindex API**

Modify `backend/app/api/documents.py` to support:

- `PATCH /api/documents/{document_id}` for metadata edits.
- `POST /api/documents/{document_id}/reindex`.

- [ ] **Step 7: Add debug search API**

Create `backend/app/api/rag.py` with `POST /api/rag/search`, request shape `{"query": str, "limit": int = 5}`, and response shape `{"hits": [{"content": str, "score": float, "source_type": str, "source_id": str}]}`. Include router in `main.py`.

- [ ] **Step 8: Run RAG tests**

Run:

```bash
cd backend && pytest tests/test_rag.py tests/test_documents.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/repositories/chroma_store.py backend/app/services/rag_service.py backend/app/api/rag.py backend/app/api/documents.py backend/app/services/document_service.py backend/app/main.py backend/tests/test_rag.py backend/tests/test_documents.py
git commit -m "feat: index documents for rag search"
```

### Task 7: Interview Configuration and Session Creation

**Files:**
- Create: `backend/app/prompts/question_generation.md`
- Create: `backend/app/services/interview_service.py`
- Create: `backend/app/api/interviews.py`
- Create: `backend/tests/test_interviews.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing config and session tests**

Create `backend/tests/test_interviews.py`:

```python
from fastapi.testclient import TestClient


CONFIG = {
    "target_company": "OpenAI",
    "target_role": "Backend Engineer",
    "job_description": "Build reliable AI application backends.",
    "extra_prompt": "Focus on RAG and FastAPI.",
    "mode": "comprehensive",
    "chat_model_provider": "openai",
    "chat_model": "gpt-4.1-mini",
    "target_rounds": 3,
}


def test_save_last_config_and_create_session(client: TestClient) -> None:
    saved = client.put("/api/interview-configs/last", json=CONFIG)
    assert saved.status_code == 200
    loaded = client.get("/api/interview-configs/last")
    assert loaded.status_code == 200
    assert loaded.json()["target_company"] == "OpenAI"

    created = client.post("/api/interview-sessions", json=CONFIG)
    assert created.status_code == 200
    body = created.json()
    assert body["session"]["status"] == "active"
    assert body["turn"]["round_index"] == 1
    assert body["turn"]["question"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && pytest tests/test_interviews.py::test_save_last_config_and_create_session -v
```

Expected: FAIL with 404 for interview endpoints.

- [ ] **Step 3: Add question generation prompt**

Create `backend/app/prompts/question_generation.md` with instructions to use target company, role, JD, mode, retrieved document context, and memory context. Require one concise interview question as output.

- [ ] **Step 4: Implement interview service config methods**

Create `backend/app/services/interview_service.py` with:

- `get_last_config(session)`
- `save_last_config(session, config_in)`
- `create_session(session, config_in)`

`create_session` must retrieve RAG context using JD plus role, call `ModelService.generate_question`, create an `interview_sessions` row, and create the first `interview_turns` row with `round_index=1`.

- [ ] **Step 5: Implement interview API**

Create `backend/app/api/interviews.py` with:

- `GET /api/interview-configs/last`
- `PUT /api/interview-configs/last`
- `POST /api/interview-sessions`
- `GET /api/interview-sessions/{session_id}`

Include router in `backend/app/main.py`.

- [ ] **Step 6: Run interview config tests**

Run:

```bash
cd backend && pytest tests/test_interviews.py::test_save_last_config_and_create_session -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/prompts/question_generation.md backend/app/services/interview_service.py backend/app/api/interviews.py backend/app/main.py backend/tests/test_interviews.py
git commit -m "feat: create interview sessions"
```

### Task 8: Answer Feedback, Follow-Up, and Round Progression

**Files:**
- Create: `backend/app/prompts/answer_feedback.md`
- Modify: `backend/app/services/interview_service.py`
- Modify: `backend/app/api/interviews.py`
- Modify: `backend/tests/test_interviews.py`

- [ ] **Step 1: Add failing answer flow test**

Append to `backend/tests/test_interviews.py`:

```python
def test_answer_feedback_follow_up_and_next_question(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]

    answer = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I would design services around clear repository and service boundaries."},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["feedback"]
    assert isinstance(body["missing_points"], list)
    assert body["follow_up_question"]
    assert isinstance(body["weaknesses"], list)
    assert isinstance(body["review_suggestions"], list)

    follow_up = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer",
        json={"answer": "I would add retries, timeouts, and structured errors."},
    )
    assert follow_up.status_code == 200

    next_question = client.post(f"/api/interview-sessions/{session_id}/next-question")
    assert next_question.status_code == 200
    assert next_question.json()["turn"]["round_index"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && pytest tests/test_interviews.py::test_answer_feedback_follow_up_and_next_question -v
```

Expected: FAIL with 404 or missing answer endpoints.

- [ ] **Step 3: Add answer feedback prompt**

Create `backend/app/prompts/answer_feedback.md` requiring strict JSON:

```json
{
  "feedback": "string",
  "missing_points": ["string"],
  "follow_up_question": "string",
  "weaknesses": ["string"],
  "review_suggestions": ["string"]
}
```

- [ ] **Step 4: Implement answer evaluation**

Modify `InterviewService` with:

- `submit_answer(session, session_id, answer)`
- `submit_follow_up_answer(session, session_id, answer)`
- `next_question(session, session_id)`

Enforce active session state. Return HTTP 409 when the session is completed or cancelled.

- [ ] **Step 5: Implement answer APIs**

Modify `backend/app/api/interviews.py` with:

- `POST /api/interview-sessions/{session_id}/answer`
- `POST /api/interview-sessions/{session_id}/follow-up-answer`
- `POST /api/interview-sessions/{session_id}/next-question`

- [ ] **Step 6: Add state conflict test**

Append:

```python
def test_completed_session_rejects_answer(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]
    client.post(f"/api/interview-sessions/{session_id}/finish")
    response = client.post(f"/api/interview-sessions/{session_id}/answer", json={"answer": "late"})
    assert response.status_code == 409
```

This test will pass after Task 9 adds finish behavior; until then, keep it skipped with `pytest.mark.skip(reason="finish endpoint lands in report task")` and remove the skip in Task 9.

- [ ] **Step 7: Run answer flow tests**

Run:

```bash
cd backend && pytest tests/test_interviews.py -v
```

Expected: PASS except the explicitly skipped completed-session conflict test.

- [ ] **Step 8: Commit**

```bash
git add backend/app/prompts/answer_feedback.md backend/app/services/interview_service.py backend/app/api/interviews.py backend/tests/test_interviews.py
git commit -m "feat: evaluate interview answers"
```

### Task 9: Reports, Memory Files, and Finish Flow

**Files:**
- Create: `backend/app/prompts/report_generation.md`
- Create: `backend/app/prompts/memory_update.md`
- Create: `backend/app/services/memory_service.py`
- Create: `backend/app/api/reports.py`
- Create: `backend/app/api/memory.py`
- Create: `backend/tests/test_memory.py`
- Modify: `backend/app/services/interview_service.py`
- Modify: `backend/app/api/interviews.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_interviews.py`

- [ ] **Step 1: Write failing finish and memory test**

Create `backend/tests/test_memory.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings


def test_finish_generates_report_and_updates_memory(client: TestClient) -> None:
    config = {
        "target_company": "OpenAI",
        "target_role": "Backend Engineer",
        "job_description": "Build AI app infrastructure.",
        "extra_prompt": "Focus on weakness reinforcement.",
        "mode": "weakness_reinforcement",
        "chat_model_provider": "openai",
        "chat_model": "gpt-4.1-mini",
        "target_rounds": 1,
    }
    created = client.post("/api/interview-sessions", json=config).json()
    session_id = created["session"]["id"]
    client.post(f"/api/interview-sessions/{session_id}/answer", json={"answer": "I use tests and clear services."})

    finished = client.post(f"/api/interview-sessions/{session_id}/finish")
    assert finished.status_code == 200
    body = finished.json()
    assert body["report"]["report_path"].endswith(".md")

    settings = get_settings()
    assert Path(body["report"]["report_path"]).exists()
    assert (settings.data_dir / "memory" / "weakness_memory.md").exists()
    assert (settings.data_dir / "memory" / "interview_history.md").exists()
    assert (settings.data_dir / "memory" / "learning_profile.md").exists()

    memory = client.get("/api/memory")
    assert memory.status_code == 200
    assert "Weakness Memory" in memory.json()["files"]["weakness"]["content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && pytest tests/test_memory.py::test_finish_generates_report_and_updates_memory -v
```

Expected: FAIL with 404 for finish or memory endpoints.

- [ ] **Step 3: Add report and memory prompts**

Create `report_generation.md` instructing the model to produce Markdown sections:

- Summary
- Strong Signals
- Missing Points
- Weaknesses
- Review Plan
- Source Context

Create `memory_update.md` instructing the model to update current summaries and append dated records for the three fixed files.

- [ ] **Step 4: Implement memory service**

Create `backend/app/services/memory_service.py` with:

- `ensure_memory_files(settings)`
- `generate_report(session, interview_session_id)`
- `finish_session(session, interview_session_id)`
- `read_memory(settings)`
- `index_report_and_memory(session, report, memory_files)`

Use the fixed headings from the design spec. Rewrite only current summary sections and append dated history entries.

- [ ] **Step 5: Add finish endpoint**

Modify `InterviewService` and `backend/app/api/interviews.py` to implement:

- `POST /api/interview-sessions/{session_id}/finish`

Set session status to `completed`, set `ended_at`, write report, update memory, and reindex report plus memory through `RagService`.

- [ ] **Step 6: Add reports and memory APIs**

Create:

- `GET /api/reports`
- `GET /api/reports/{report_id}`
- `GET /api/memory`

Include routers in `backend/app/main.py`.

- [ ] **Step 7: Enable completed-session conflict test**

Remove the skip from `test_completed_session_rejects_answer` in `backend/tests/test_interviews.py`.

- [ ] **Step 8: Run memory and interview tests**

Run:

```bash
cd backend && pytest tests/test_memory.py tests/test_interviews.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/prompts/report_generation.md backend/app/prompts/memory_update.md backend/app/services/memory_service.py backend/app/api/reports.py backend/app/api/memory.py backend/app/services/interview_service.py backend/app/api/interviews.py backend/app/main.py backend/tests/test_memory.py backend/tests/test_interviews.py
git commit -m "feat: generate reports and memory"
```

### Task 10: Frontend Foundation and API Client

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/Dockerfile`
- Create: `frontend/next.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/app/layout.tsx`
- Create: `frontend/src/app/globals.css`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/types.ts`
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/components/StatusPill.tsx`
- Create: `frontend/src/components/MarkdownView.tsx`

- [ ] **Step 1: Create Next.js project files**

Create a Next.js App Router TypeScript app under `frontend/` with scripts:

```json
{
  "scripts": {
    "dev": "next dev --hostname 0.0.0.0",
    "build": "next build",
    "start": "next start --hostname 0.0.0.0",
    "lint": "next lint",
    "test": "vitest run"
  }
}
```

Dependencies must include `next`, `react`, `react-dom`, `lucide-react`, and `react-markdown`. Dev dependencies must include `typescript`, `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, and `jsdom`.

- [ ] **Step 2: Add shared frontend types**

Create `frontend/src/lib/types.ts` with TypeScript interfaces matching backend responses for document, model provider, interview config, session, turn, report, and memory file content.

- [ ] **Step 3: Add API client**

Create `frontend/src/lib/api.ts` with:

```ts
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE_URL}${path}`, { method: init?.method, body: init?.body, headers });
  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(errorBody || `Request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}
```

Add a separate `uploadDocument(file: File)` helper that sends `FormData` without forcing `Content-Type`.

- [ ] **Step 4: Add app shell**

Create `AppShell` with navigation links for Dashboard, Library, Interview, and Review. Use lucide icons and clear active states.

- [ ] **Step 5: Add global styles**

Create a restrained workbench visual style in `globals.css`: neutral background, high-contrast text, compact panels, 8px border radius, responsive layout, and no decorative gradient orbs.

- [ ] **Step 6: Run frontend tests and build**

Run:

```bash
cd frontend && npm test
cd frontend && npm run build
```

Expected: tests pass and build exits 0.

- [ ] **Step 7: Commit**

```bash
git add frontend
git commit -m "feat: add frontend foundation"
```

### Task 11: Frontend Library and Document Detail

**Files:**
- Create: `frontend/src/app/library/page.tsx`
- Create: `frontend/src/app/library/[documentId]/page.tsx`
- Create: `frontend/src/components/DocumentUploader.tsx`
- Create: `frontend/src/components/__tests__/DocumentUploader.test.tsx`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Write failing uploader test**

Create `frontend/src/components/__tests__/DocumentUploader.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DocumentUploader } from "../DocumentUploader";

describe("DocumentUploader", () => {
  it("shows markdown and txt upload guidance", () => {
    render(<DocumentUploader onUploaded={() => undefined} />);
    expect(screen.getByText(/Markdown\/TXT/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upload/i })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend && npm test -- DocumentUploader.test.tsx
```

Expected: FAIL because `DocumentUploader` does not exist.

- [ ] **Step 3: Implement uploader component**

Create `DocumentUploader` with file input accepting `.md,.txt`, disabled submit until a file is selected, loading state during upload, and success callback with created document.

- [ ] **Step 4: Implement Library page**

Create `/library` page that fetches `GET /api/documents`, displays upload control, filters by keyword/tag, and lists documents with title, summary, tags, analysis status, index status, and update time.

- [ ] **Step 5: Implement Document Detail page**

Create `/library/[documentId]` page that fetches document detail, edits title/summary/tags/knowledge points/weakness candidates, calls `PATCH /api/documents/{id}`, and provides `Save and Reindex` using `POST /api/documents/{id}/reindex`.

- [ ] **Step 6: Run frontend verification**

Run:

```bash
cd frontend && npm test
cd frontend && npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/library frontend/src/components/DocumentUploader.tsx frontend/src/components/__tests__/DocumentUploader.test.tsx frontend/src/lib/api.ts
git commit -m "feat: add document library UI"
```

### Task 12: Frontend Interview Workspace

**Files:**
- Create: `frontend/src/app/interview/page.tsx`
- Create: `frontend/src/components/InterviewWorkspace.tsx`
- Create: `frontend/src/components/__tests__/InterviewWorkspace.test.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`

- [ ] **Step 1: Write failing interview workspace test**

Create `frontend/src/components/__tests__/InterviewWorkspace.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InterviewWorkspace } from "../InterviewWorkspace";

describe("InterviewWorkspace", () => {
  it("renders configuration and answer areas", () => {
    render(<InterviewWorkspace />);
    expect(screen.getByLabelText(/Target company/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Target role/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start interview/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend && npm test -- InterviewWorkspace.test.tsx
```

Expected: FAIL because `InterviewWorkspace` does not exist.

- [ ] **Step 3: Implement interview API helpers**

Add helpers for:

- `getModels()`
- `getLastInterviewConfig()`
- `saveLastInterviewConfig(config)`
- `createInterviewSession(config)`
- `submitAnswer(sessionId, answer)`
- `submitFollowUpAnswer(sessionId, answer)`
- `nextQuestion(sessionId)`
- `finishInterview(sessionId)`

- [ ] **Step 4: Implement interview workspace**

Create `InterviewWorkspace` with:

- config form fields for company, role, JD, extra prompt, mode, model, target rounds;
- model dropdown populated by `GET /api/models`;
- load-last-config behavior;
- active session panel for question, answer, feedback, missing points, follow-up question, follow-up answer, next question, and finish.

- [ ] **Step 5: Implement `/interview` page**

Render `InterviewWorkspace` inside `AppShell`.

- [ ] **Step 6: Run frontend verification**

Run:

```bash
cd frontend && npm test
cd frontend && npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/interview/page.tsx frontend/src/components/InterviewWorkspace.tsx frontend/src/components/__tests__/InterviewWorkspace.test.tsx frontend/src/lib/api.ts frontend/src/lib/types.ts
git commit -m "feat: add interview workspace UI"
```

### Task 13: Dashboard, Review, and Memory UI

**Files:**
- Create: `frontend/src/app/page.tsx`
- Create: `frontend/src/app/review/page.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/components/MarkdownView.tsx`

- [ ] **Step 1: Implement Markdown renderer test**

Add a component test for `MarkdownView` that renders `# Report` as a heading.

- [ ] **Step 2: Implement Dashboard page**

Create `/` page that fetches:

- `GET /api/health`
- `GET /api/documents`
- `GET /api/memory`
- `GET /api/reports`

Render document count, model availability, latest weakness summary, latest report link, and primary actions.

- [ ] **Step 3: Implement Review page**

Create `/review` page with report list, selected report Markdown preview, and memory tabs for weakness, interview history, and learning profile.

- [ ] **Step 4: Run frontend verification**

Run:

```bash
cd frontend && npm test
cd frontend && npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/page.tsx frontend/src/app/review/page.tsx frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/components/MarkdownView.tsx
git commit -m "feat: add dashboard and review UI"
```

### Task 14: End-to-End Docker Verification and Documentation

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `backend/tests/test_memory.py`
- Modify: `frontend/src/app/interview/page.tsx`

- [ ] **Step 1: Run full backend tests**

Run:

```bash
cd backend && pytest -v
```

Expected: PASS.

- [ ] **Step 2: Run frontend tests and build**

Run:

```bash
cd frontend && npm test
cd frontend && npm run build
```

Expected: PASS.

- [ ] **Step 3: Validate Compose config**

Run:

```bash
docker compose --env-file .env.example config
```

Expected: exits 0 and includes `backend`, `frontend`, and `./data:/app/data`.

- [ ] **Step 4: Start stack**

Run:

```bash
cp .env.example .env
docker compose up --build
```

Expected:

- Frontend available at `http://localhost:3000`.
- Backend health available at `http://localhost:8000/api/health`.
- Backend reports missing model providers when API keys are empty without exposing secret values.

- [ ] **Step 5: Manual smoke flow**

In the browser:

1. Open Dashboard.
2. Navigate to Library.
3. Upload `sample.md` containing:

```markdown
# RAG Project Notes

I built a FastAPI service that stores Markdown documents, indexes them in Chroma, and retrieves context for interview preparation.
```

4. Confirm document appears with completed analysis and index status.
5. Open Interview.
6. Use target company `OpenAI`, role `Backend Engineer`, mode `Comprehensive`, and target rounds `1`.
7. Start interview, answer one question, finish interview.
8. Open Review and confirm report plus memory content are visible.

- [ ] **Step 6: Update README**

Document:

- local setup;
- environment variables;
- how model provider availability works;
- API keys are backend environment variables only;
- supported upload formats;
- known v1 exclusions;
- smoke test steps.

- [ ] **Step 7: Commit**

```bash
git add README.md .env.example docker-compose.yml backend frontend
git commit -m "docs: document local verification"
```

## Plan Self-Review Checklist

- Spec coverage: Tasks 1-14 cover local Docker startup, backend services, SQLite, Markdown files, Chroma RAG, model selection, document upload, AI metadata analysis, interview flow, answer feedback, report generation, memory updates, and all five frontend pages.
- Provider safety: API keys are read only from backend environment variables and are not returned by model availability responses.
- v1 exclusions: no login, permissions, multi-user support, PDF/Word, audio/video, Redis, Celery, MySQL, frontend API key entry, database API key storage, or complex scoring tasks are included.
- Type consistency: interview modes use `comprehensive`, `project_deep_dive`, `knowledge_drill`, and `weakness_reinforcement` across backend schemas, API tests, and frontend config.
- Execution sequencing: backend contracts land before frontend API integration, and Docker verification happens after both services exist.
