# MySQL, Qdrant, and Startup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace production SQLite and Chroma storage with MySQL and Qdrant, and provide Wegent-style `./start.sh` lifecycle commands plus a full-container Compose mode on non-conflicting ports.

**Architecture:** FastAPI remains the control plane and executes RAG in process through a small `VectorStore` protocol backed only by Qdrant. Development runs FastAPI and Next.js on the host while Compose runs MySQL and Qdrant; production-style Compose runs all four services. Alembic owns relational schema creation, and a testable Python lifecycle manager sits behind the root `start.sh` entrypoint.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PyMySQL, qdrant-client, MySQL 8.4, Qdrant 1.17, Docker Compose v2, Next.js 16, pytest, Vitest

---

## File Structure

### Backend storage

- Modify `backend/pyproject.toml` and `backend/uv.lock`: replace Chroma with Alembic, PyMySQL, and qdrant-client.
- Modify `backend/app/core/config.py`: define database and Qdrant settings; remove SQLite and Chroma paths.
- Modify `backend/app/db/session.py`: build engines from `DATABASE_URL`; stop creating production schemas at app startup.
- Modify `backend/app/db/models.py`: use storage-neutral document chunk field names.
- Rename `backend/app/repositories/sqlite.py` to `backend/app/repositories/database.py`: keep SQLAlchemy repositories independent of the database driver.
- Create `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, and `backend/alembic/versions/20260620_0001_initial_schema.py`: establish the MySQL schema baseline.

### Vector storage and RAG

- Create `backend/app/repositories/vector_store.py`: define vector chunk/search records, errors, protocol, and stable point IDs.
- Create `backend/app/repositories/qdrant_store.py`: implement collection management, upsert, filtered deletion, and search.
- Delete `backend/app/repositories/chroma_store.py`.
- Modify `backend/app/services/rag_service.py`, `backend/app/services/memory_service.py`, and `backend/app/services/document_service.py`: use the vector protocol and persist failed indexing state.
- Modify backend APIs, repositories imports, health output, and tests to use storage-neutral names.

### Runtime and documentation

- Modify `docker-compose.yml`, `.env.example`, `backend/Dockerfile`, and `frontend/Dockerfile`: add healthy MySQL/Qdrant dependencies and full-container startup.
- Create `scripts/start.py`: implement prerequisite checks, dependency orchestration, process state, health waits, and lifecycle commands.
- Create root `start.sh`: provide the stable executable entrypoint.
- Modify `.gitignore`: ignore `.pids/` and `logs/`.
- Modify `README.md`: document development and full-container commands, ports, configuration, and persistence.
- Create `backend/tests/test_startup_manager.py`: test lifecycle helpers without launching real services.

---

### Task 1: Database Configuration and Storage-Neutral Repositories

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/db/session.py`
- Modify: `backend/app/db/models.py`
- Rename: `backend/app/repositories/sqlite.py` to `backend/app/repositories/database.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/documents.py`
- Modify: `backend/app/api/reports.py`
- Modify: `backend/app/services/document_service.py`
- Modify: `backend/app/services/interview_service.py`
- Modify: `backend/app/services/memory_service.py`
- Modify: `backend/app/services/rag_service.py`
- Create: `backend/tests/test_database.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_schema.py`

- [ ] **Step 1: Write failing settings and engine tests**

Create `backend/tests/test_database.py`:

```python
from sqlalchemy.engine import Engine

from app.core.config import Settings
from app.db.session import create_engine_for_settings


def test_settings_expose_mysql_and_qdrant_configuration(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url="mysql+pymysql://auto_reign:secret@127.0.0.1:13306/auto_reign",
        qdrant_url="http://127.0.0.1:16333",
        qdrant_collection="auto_reign_default",
    )

    assert settings.database_url.startswith("mysql+pymysql://")
    assert settings.qdrant_url == "http://127.0.0.1:16333"
    assert settings.qdrant_collection == "auto_reign_default"
    assert not hasattr(settings, "sqlite_path")
    assert not hasattr(settings, "chroma_dir")


def test_engine_uses_database_url_without_connecting(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url="mysql+pymysql://auto_reign:secret@127.0.0.1:13306/auto_reign",
    )

    engine: Engine = create_engine_for_settings(settings)

    assert engine.url.drivername == "mysql+pymysql"
    assert engine.pool._pre_ping is True
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```bash
cd backend
uv run pytest tests/test_database.py -v
```

Expected: FAIL because `database_url`, `qdrant_url`, and `qdrant_collection` do not exist and the engine still reads `sqlite_path`.

- [ ] **Step 3: Replace dependencies and implement database configuration**

Run:

```bash
cd backend
uv remove chromadb
uv add "alembic>=1.16,<2" "pymysql>=1.1,<2" "qdrant-client>=1.17,<2"
```

Change `Settings` in `backend/app/core/config.py` to include:

```python
data_dir: Path = Path("data")
database_url: str = "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign"
qdrant_url: str = "http://127.0.0.1:16333"
qdrant_collection: str = "auto_reign_default"
```

Remove `sqlite_path`, `chroma_dir`, and `default_collection`. Keep `ensure_data_dirs()` limited to `uploads`, `reports`, and `memory`.

Replace `create_engine_for_settings()` with:

```python
def create_engine_for_settings(settings: Settings) -> Engine:
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(settings.database_url, **kwargs)
```

Remove `init_db()` from production code and remove its call from `create_app()`.

- [ ] **Step 4: Rename repositories and chunk columns**

Run:

```bash
git mv backend/app/repositories/sqlite.py backend/app/repositories/database.py
```

Replace every `app.repositories.sqlite` import with `app.repositories.database`.
In `DocumentChunk`, replace the two storage-specific columns with:

```python
vector_collection: Mapped[str] = mapped_column(String(120))
vector_id: Mapped[str] = mapped_column(String(255), unique=True)
```

Update test setup to use a file SQLite URL strictly for unit tests:

```python
monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
monkeypatch.setenv("QDRANT_URL", ":memory:")
monkeypatch.setenv("QDRANT_COLLECTION", "auto_reign_test")
```

After `get_settings.cache_clear()`, create unit-test tables explicitly:

```python
settings = get_settings()
engine = create_engine_for_settings(settings)
Base.metadata.create_all(engine)
```

- [ ] **Step 5: Run database and existing schema tests**

Run:

```bash
cd backend
uv run pytest tests/test_database.py tests/test_schema.py -v
uv run ruff check app tests
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 6: Commit database configuration**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app backend/tests
git commit -m "Use configurable relational database storage"
```

---

### Task 2: Alembic Schema Baseline

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/versions/20260620_0001_initial_schema.py`
- Modify: `backend/tests/test_schema.py`

- [ ] **Step 1: Replace the schema test with an Alembic migration test**

Use an isolated SQLite URL for the fast migration test while preserving the same metadata:

```python
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


EXPECTED_TABLES = {
    "alembic_version",
    "documents",
    "document_chunks",
    "interview_configs",
    "interview_sessions",
    "interview_turns",
    "reports",
    "memory_files",
}


def test_alembic_upgrade_creates_required_tables(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))

    command.upgrade(config, "head")

    assert EXPECTED_TABLES.issubset(inspect(create_engine(database_url)).get_table_names())
```

- [ ] **Step 2: Run the migration test and confirm it fails**

Run:

```bash
cd backend
uv run pytest tests/test_schema.py -v
```

Expected: FAIL because `backend/alembic.ini` does not exist.

- [ ] **Step 3: Configure Alembic to read `DATABASE_URL`**

Create `backend/alembic.ini` with `script_location = %(here)s/alembic` and no committed password. In `backend/alembic/env.py`, use:

```python
from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Add an explicit initial migration**

Create revision `20260620_0001` with `down_revision = None`. Its `upgrade()` must call `op.create_table()` for the seven application tables using the exact names, column lengths, JSON columns, foreign keys, delete rules, and unique `document_chunks.vector_id` constraint in `app.db.models`. Create referenced tables before dependent tables. Its `downgrade()` must drop them in this order:

```python
for table_name in (
    "memory_files",
    "reports",
    "interview_turns",
    "interview_sessions",
    "interview_configs",
    "document_chunks",
    "documents",
):
    op.drop_table(table_name)
```

Verify the generated SQL includes MySQL-compatible `VARCHAR` lengths and JSON columns:

```bash
cd backend
DATABASE_URL=mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign \
  uv run alembic upgrade head --sql > /tmp/auto-reign-initial-schema.sql
rg "CREATE TABLE|JSON|vector_id" /tmp/auto-reign-initial-schema.sql
```

Expected: seven `CREATE TABLE` statements plus the Alembic version table, JSON columns, and `vector_id`.

- [ ] **Step 5: Run migration tests and commit**

```bash
cd backend
uv run pytest tests/test_schema.py -v
uv run ruff check alembic app tests
cd ..
git add backend/alembic.ini backend/alembic backend/tests/test_schema.py
git commit -m "Add initial Alembic schema"
```

Expected: migration test and Ruff pass.

---

### Task 3: Qdrant Vector Store Adapter

**Files:**
- Create: `backend/app/repositories/vector_store.py`
- Create: `backend/app/repositories/qdrant_store.py`
- Create: `backend/tests/test_qdrant_store.py`
- Delete: `backend/app/repositories/chroma_store.py`

- [ ] **Step 1: Write failing vector adapter tests**

Cover stable UUID generation, lazy cosine collection creation, upsert payloads, filtered document deletion, score mapping, absent-collection search, vector dimension conflicts, and client failures. The core test shape is:

```python
def test_upsert_creates_cosine_collection_and_writes_payload() -> None:
    client = FakeQdrantClient(collection_exists=False)
    store = QdrantVectorStore(client=client)
    chunk = VectorChunk(
        id=stable_vector_id("document", "doc-1", 0),
        content="FastAPI dependency injection",
        embedding=[0.1, 0.2],
        metadata={"source_type": "document", "document_id": "doc-1"},
    )

    store.upsert_chunks("auto_reign_test", [chunk])

    assert client.created_size == 2
    assert client.created_distance == Distance.COSINE
    assert client.points[0].payload["content"] == chunk.content


def test_existing_collection_dimension_mismatch_is_explicit() -> None:
    client = FakeQdrantClient(collection_exists=True, vector_size=3)
    store = QdrantVectorStore(client=client)

    with pytest.raises(VectorDimensionMismatch):
        store.upsert_chunks("auto_reign_test", [make_chunk([0.1, 0.2])])
```

- [ ] **Step 2: Run tests and confirm missing adapter failures**

```bash
cd backend
uv run pytest tests/test_qdrant_store.py -v
```

Expected: collection error because the vector modules do not exist.

- [ ] **Step 3: Define the storage-neutral protocol**

Create these public contracts in `vector_store.py`:

```python
@dataclass(frozen=True)
class VectorChunk:
    id: str
    content: str
    embedding: list[float]
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class VectorSearchHit:
    content: str
    score: float
    metadata: dict[str, Any]


class VectorStoreError(RuntimeError):
    pass


class VectorStoreUnavailable(VectorStoreError):
    pass


class VectorDimensionMismatch(VectorStoreError):
    pass


class VectorStore(Protocol):
    def upsert_chunks(self, collection_name: str, chunks: list[VectorChunk]) -> None:
        raise NotImplementedError

    def delete_document_chunks(self, collection_name: str, document_id: str) -> None:
        raise NotImplementedError

    def search(
        self, collection_name: str, query_embedding: list[float], limit: int
    ) -> list[VectorSearchHit]:
        raise NotImplementedError


def stable_vector_id(source_type: str, source_id: str, chunk_index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"auto-reign:{source_type}:{source_id}:{chunk_index}"))
```

- [ ] **Step 4: Implement Qdrant operations**

`QdrantVectorStore` accepts an injected `QdrantClient`. The default factory uses `QdrantClient(location=":memory:")` only when `QDRANT_URL=:memory:` for tests; otherwise it uses `QdrantClient(url=settings.qdrant_url)`. Cache the default store with `@lru_cache` so request-created services share one client.

Use unnamed cosine vectors:

```python
self.client.create_collection(
    collection_name=collection_name,
    vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
)
```

Upsert `models.PointStruct` values with the chunk content added to payload. Delete by `document_id` using `models.FilterSelector`, `models.Filter`, `models.FieldCondition`, and `models.MatchValue`. Search with:

```python
response = self.client.query_points(
    collection_name=collection_name,
    query=query_embedding,
    limit=limit,
    with_payload=True,
)
```

Map client transport/HTTP failures to `VectorStoreUnavailable`. Re-raise `VectorDimensionMismatch` unchanged so it is not misreported as a connection problem.

- [ ] **Step 5: Run adapter tests, remove Chroma, and commit**

```bash
cd backend
uv run pytest tests/test_qdrant_store.py -v
uv run ruff check app/repositories tests/test_qdrant_store.py
cd ..
git rm backend/app/repositories/chroma_store.py
git add backend/app/repositories backend/tests/test_qdrant_store.py
git commit -m "Add Qdrant vector storage"
```

Expected: all Qdrant adapter tests pass and no Chroma implementation remains.

---

### Task 4: Move RAG, Documents, and Memory to Qdrant

**Files:**
- Modify: `backend/app/services/rag_service.py`
- Modify: `backend/app/services/memory_service.py`
- Modify: `backend/app/services/document_service.py`
- Modify: `backend/app/core/errors.py`
- Modify: `backend/app/api/health.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_rag.py`
- Modify: `backend/tests/test_documents.py`
- Modify: `backend/tests/test_health_and_models.py`
- Modify: `backend/tests/test_model_service.py`
- Modify: `backend/tests/test_memory.py`

- [ ] **Step 1: Rewrite RAG tests against `VectorStore` and add failure-state tests**

Replace `FakeChromaStore` with a fake implementing the protocol. Assert the configured collection is `auto_reign_default`, stable point IDs are UUIDs, and search preserves the existing response shape. Add:

```python
def test_index_failure_is_committed_as_failed(client, monkeypatch) -> None:
    def fail_upsert(*args, **kwargs):
        raise VectorStoreUnavailable("qdrant unavailable")

    monkeypatch.setattr(QdrantVectorStore, "upsert_chunks", fail_upsert)
    response = client.post(
        "/api/documents/upload",
        files={"file": ("notes.txt", b"RAG notes", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["index_status"] == "failed"
    persisted = client.get(f"/api/documents/{response.json()['id']}")
    assert persisted.json()["index_status"] == "failed"
```

Add a search test expecting HTTP 503 and code `vector_store_unavailable` when the store raises `VectorStoreUnavailable`.

- [ ] **Step 2: Run focused service tests and confirm old Chroma references fail**

```bash
cd backend
uv run pytest tests/test_rag.py tests/test_documents.py tests/test_memory.py -v
```

Expected: FAIL because services still import `chroma_store` and use Chroma-specific attributes.

- [ ] **Step 3: Inject and use `VectorStore` in `RagService`**

Change constructor state to:

```python
self.vector_store = vector_store or get_qdrant_store()
```

Use `settings.qdrant_collection`, `VectorChunk`, and `stable_vector_id()` throughout. Store `vector_collection` and `vector_id` in `DocumentChunk`. Wrap document indexing operations that can fail due to file reading, embedding, or vector storage:

```python
try:
    # read, split, embed, delete old chunks, upsert, and add relational chunks
except (OSError, HTTPException, VectorStoreError):
    document.index_status = "failed"
    session.flush()
    return document
```

On search, map `VectorStoreUnavailable` to HTTP 503 code `vector_store_unavailable`, and map `VectorDimensionMismatch` to HTTP 503 code `vector_dimension_mismatch`. Do not return an empty success for these failures.

- [ ] **Step 4: Migrate report and memory indexing**

In `MemoryService`, build `VectorChunk` records with stable UUIDs:

```python
id=stable_vector_id("report", report.id, index)
id=stable_vector_id("memory", memory_file.id, index)
```

Call `self.rag_service.vector_store.upsert_chunks(settings.qdrant_collection, chunks)` once after building all report and memory chunks.

- [ ] **Step 5: Update health and remove all runtime Chroma/SQLite names**

Return:

```python
"storage": {"mysql": "configured", "qdrant": "configured"}
```

Update all direct `Settings(...)` test instances to use `database_url`, `qdrant_url`, and `qdrant_collection`. Clear the cached Qdrant store before and after each TestClient fixture so in-memory test collections cannot leak across tests.

- [ ] **Step 6: Run the backend suite and commit**

```bash
cd backend
uv run pytest -v
uv run ruff check .
cd ..
rg -n "chromadb|ChromaStore|chroma_store|sqlite_path|chroma_dir|repositories\.sqlite" backend
```

Expected: all tests and Ruff pass; the final search returns no matches.

```bash
git add backend
git commit -m "Route RAG indexing through Qdrant"
```

---

### Task 5: Add MySQL and Qdrant Compose Services

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `backend/Dockerfile`
- Modify: `frontend/Dockerfile`
- Create: `backend/tests/integration/test_mysql_schema.py`

- [ ] **Step 1: Write Compose contract and MySQL schema checks**

Create an integration test skipped unless `RUN_MYSQL_INTEGRATION=1`. It connects using `DATABASE_URL`, inspects tables, and asserts `EXPECTED_TABLES - {"alembic_version"}` exists. Add a shell-level Compose assertion command to the task:

```bash
docker compose config --services
```

Expected after implementation: exactly `mysql`, `qdrant`, `backend`, and `frontend`.

- [ ] **Step 2: Update environment defaults**

Use these non-conflicting host defaults:

```dotenv
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8300
FRONTEND_PORT=3100
MYSQL_PORT=13306
QDRANT_HTTP_PORT=16333
QDRANT_GRPC_PORT=16334
MYSQL_DATABASE=auto_reign
MYSQL_USER=auto_reign
MYSQL_PASSWORD=auto_reign
MYSQL_ROOT_PASSWORD=auto_reign_root
DATABASE_URL=mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign
QDRANT_URL=http://127.0.0.1:16333
QDRANT_COLLECTION=auto_reign_default
DATA_DIR=./data
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8300
```

Retain empty provider keys and model configuration. Remove `SQLITE_PATH`, `CHROMA_DIR`, and `DEFAULT_COLLECTION`.

- [ ] **Step 3: Define healthy dependency and application services**

Use Compose project name `auto-reign`. MySQL uses `mysql:8.4`, Qdrant uses `qdrant/qdrant:v1.17.0`, and named volumes `mysql_data` and `qdrant_data`. Publish the four configured host ports. MySQL health uses `mysqladmin ping`; Qdrant health uses its HTTP readiness endpoint.

Override container backend values so service DNS names are used:

```yaml
environment:
  DATABASE_URL: mysql+pymysql://${MYSQL_USER:-auto_reign}:${MYSQL_PASSWORD:-auto_reign}@mysql:3306/${MYSQL_DATABASE:-auto_reign}
  QDRANT_URL: http://qdrant:6333
  DATA_DIR: /app/data
depends_on:
  mysql:
    condition: service_healthy
  qdrant:
    condition: service_healthy
```

The backend image must copy `alembic.ini` and `alembic/`, then run:

```dockerfile
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

Add backend and frontend health checks using Python `urllib.request` and Node `fetch`, respectively. Make frontend depend on a healthy backend.

- [ ] **Step 4: Validate and run real dependency integration tests**

```bash
cp -n .env.example .env
docker compose config
docker compose up -d mysql qdrant
docker compose ps
cd backend
DATABASE_URL=mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign \
  uv run alembic upgrade head
RUN_MYSQL_INTEGRATION=1 \
DATABASE_URL=mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign \
  uv run pytest tests/integration/test_mysql_schema.py -v
```

Expected: Compose validates, MySQL and Qdrant become healthy, migration succeeds, and the integration test passes.

- [ ] **Step 5: Commit Compose infrastructure**

```bash
git add .env.example docker-compose.yml backend/Dockerfile frontend/Dockerfile backend/tests/integration
git commit -m "Add MySQL and Qdrant runtime services"
```

---

### Task 6: Implement the Wegent-Style Lifecycle Commands

**Files:**
- Create: `scripts/start.py`
- Create: `start.sh`
- Modify: `.gitignore`
- Create: `backend/tests/test_startup_manager.py`

- [ ] **Step 1: Write failing lifecycle helper tests**

Load `scripts/start.py` with `importlib.util.spec_from_file_location`. Start with
these executable tests for environment precedence, occupied ports, and CLI
validation:

```python
def test_find_available_port_advances_past_listener() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied_port = listener.getsockname()[1]

        selected_port = start_module.find_available_port(occupied_port)

    assert selected_port > occupied_port


def test_load_env_does_not_override_exported_value(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BACKEND_PORT=8300\nMYSQL_PASSWORD=file-secret\n")
    environ = {"MYSQL_PASSWORD": "exported-secret"}

    start_module.load_env(env_file, environ)

    assert environ["BACKEND_PORT"] == "8300"
    assert environ["MYSQL_PASSWORD"] == "exported-secret"


def test_invalid_option_returns_usage_error() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--unknown"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr.lower()


def test_stale_pid_state_is_removed_without_signal(tmp_path) -> None:
    state_path = tmp_path / "backend.json"
    state_path.write_text('{"pid": 42, "port": 8300, "marker": "expected"}')
    signals: list[tuple[int, int]] = []

    stopped = start_module.stop_managed_process(
        state_path,
        command_for_pid=lambda pid: "different command",
        signal_group=lambda pid, sig: signals.append((pid, sig)),
    )

    assert stopped is False
    assert signals == []
    assert not state_path.exists()


def test_stop_signals_only_matching_process_group(tmp_path) -> None:
    state_path = tmp_path / "backend.json"
    state_path.write_text('{"pid": 42, "port": 8300, "marker": "unique-marker"}')
    signals: list[tuple[int, int]] = []

    stopped = start_module.stop_managed_process(
        state_path,
        command_for_pid=lambda pid: "python unique-marker",
        signal_group=lambda pid, sig: signals.append((pid, sig)),
    )

    assert stopped is True
    assert signals == [(42, signal.SIGTERM)]
    assert not state_path.exists()


def test_healthy_managed_process_is_reused(tmp_path) -> None:
    state_path = tmp_path / "frontend.json"
    state_path.write_text('{"pid": 84, "port": 3100, "marker": "next-marker"}')

    state = start_module.healthy_managed_state(
        state_path,
        health_url_for=lambda item: f"http://127.0.0.1:{item.port}/",
        command_for_pid=lambda pid: "npm next-marker",
        http_probe=lambda url, timeout: True,
    )

    assert state == start_module.ServiceState(pid=84, port=3100, marker="next-marker")
```

These injected callables prevent tests from inspecting or signalling real
processes. The orchestration test passes the healthy state above to the start
path and asserts the service process factory has no calls.

- [ ] **Step 2: Run tests and confirm the missing script failure**

```bash
cd backend
uv run pytest tests/test_startup_manager.py -v
```

Expected: FAIL because `scripts/start.py` does not exist.

- [ ] **Step 3: Implement configuration, state, and process helpers**

`scripts/start.py` must use only the Python standard library. Define:

```python
@dataclass(frozen=True)
class ServiceState:
    pid: int
    port: int
    marker: str


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    pid_dir: Path
    log_dir: Path


def load_env(path: Path, environ: MutableMapping[str, str]) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        environ.setdefault(key.strip(), value)


def find_available_port(start_port: int) -> int:
    for port in range(start_port, 65536):
        with socket.socket() as candidate:
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"No available TCP port at or above {start_port}")


def read_state(path: Path) -> ServiceState | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ServiceState(
            pid=int(payload["pid"]),
            port=int(payload["port"]),
            marker=str(payload["marker"]),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None


def read_process_command(pid: int) -> str | None:
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def process_matches(state: ServiceState) -> bool:
    command = read_process_command(state.pid)
    return command is not None and state.marker in command


def stop_managed_process(
    state_path: Path,
    command_for_pid: Callable[[int], str | None] | None = None,
    signal_group: Callable[[int, int], None] | None = None,
) -> bool:
    inspect_command = command_for_pid or read_process_command
    send_signal = signal_group or os.killpg
    state = read_state(state_path)
    if state is None:
        return False
    command = inspect_command(state.pid)
    if command is None or state.marker not in command:
        state_path.unlink(missing_ok=True)
        return False
    send_signal(state.pid, signal.SIGTERM)
    state_path.unlink(missing_ok=True)
    return True


def healthy_managed_state(
    state_path: Path,
    health_url_for: Callable[[ServiceState], str],
    command_for_pid: Callable[[int], str | None] | None = None,
    http_probe: Callable[[str, float], bool] | None = None,
) -> ServiceState | None:
    inspect_command = command_for_pid or read_process_command
    probe = http_probe or wait_for_http
    state = read_state(state_path)
    if state is None:
        return None
    command = inspect_command(state.pid)
    if command is None or state.marker not in command:
        state_path.unlink(missing_ok=True)
        return None
    return state if probe(health_url_for(state), 2) else None


def wait_for_http(url: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    return False


def wait_for_tcp(host: str, port: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False
```

State files are JSON at `.pids/backend.json` and `.pids/frontend.json`. Process matching must require both a live PID and the recorded unique command marker. Start host services with `start_new_session=True`; stop with `os.killpg()` only after identity validation. Remove stale state without signalling its PID.

- [ ] **Step 4: Implement lifecycle orchestration**

Support only these CLI forms:

```text
./start.sh
./start.sh --status
./start.sh --stop
./start.sh --restart
./start.sh --help
```

Normal start must:

1. Copy `.env.example` to `.env` only when `.env` is absent.
2. Validate `docker`, `uv`, `node`, and `npm` with `shutil.which()` and validate Docker with `docker info`.
3. Run `docker compose -p auto-reign up -d mysql qdrant`.
4. Wait for configured MySQL TCP and Qdrant `/readyz` endpoints.
5. Run `uv sync` and `uv run alembic upgrade head` in `backend/`.
6. Run `npm install` only when `frontend/node_modules` is absent.
7. Reuse healthy managed backend/frontend processes; otherwise select available application ports and start them with logs at `logs/backend.log` and `logs/frontend.log`.
8. Export the selected backend URL as `NEXT_PUBLIC_API_BASE_URL` before starting Next.js.
9. Wait for `/api/health` and the frontend root, then print URLs.

`--status` prints one line each for MySQL, Qdrant, backend, and frontend. `--stop` terminates validated host process groups, then runs `docker compose -p auto-reign stop mysql qdrant`. `--restart` calls stop and then start. Failed application startup stops only host processes started by that invocation and prints the failing log path.

Process termination sends `SIGTERM`, waits up to five seconds, and sends
`SIGKILL` only if the validated process group remains alive. Command output may
include executable names, service names, ports, and log paths, but must never
print `DATABASE_URL`, provider keys, or MySQL passwords. State JSON contains
only PID, port, and the non-secret process marker.

Create `start.sh` as the portable entrypoint:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to run Auto Reign." >&2
  exit 1
fi
exec python3 "$ROOT_DIR/scripts/start.py" "$@"
```

Make it executable and ignore runtime state:

```bash
chmod +x start.sh
```

Add `.pids/` and `logs/` to `.gitignore`.

- [ ] **Step 5: Run helper and shell-interface tests**

```bash
cd backend
uv run pytest tests/test_startup_manager.py -v
cd ..
./start.sh --help
python3 -m py_compile scripts/start.py
```

Expected: tests pass, help lists all four actions, and Python compilation succeeds.

- [ ] **Step 6: Commit lifecycle management**

```bash
git add start.sh scripts/start.py .gitignore backend/tests/test_startup_manager.py
git commit -m "Add local service lifecycle commands"
```

---

### Task 7: Document the New Runtime

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update quick-start and architecture documentation**

Make `./start.sh` the first development command. Document `--status`, `--stop`, and `--restart`; list the four default host ports; describe host-process versus full-container modes; state that MySQL stores metadata, Qdrant stores vectors, and `DATA_DIR` stores uploaded/generated files. Remove claims that V1 uses SQLite or Chroma and retain the explicit exclusions for authentication, Redis, workers, object storage, and remote RAG runtime.

- [ ] **Step 2: Check documentation against executable configuration**

```bash
rg -n "3100|8300|13306|16333|start.sh|MySQL|Qdrant" README.md .env.example docker-compose.yml
rg -n "SQLite|Chroma|localhost:3000|localhost:8000" README.md .env.example
```

Expected: the first search shows consistent commands and ports; the second has no stale runtime claims.

- [ ] **Step 3: Commit documentation**

```bash
git add README.md
git commit -m "Document MySQL Qdrant startup workflow"
```

---

### Task 8: End-to-End Verification

**Files:**
- Modify only files required by failures discovered during verification.

- [ ] **Step 1: Run static and automated suites**

```bash
cd backend
uv run pytest -v
uv run ruff check .
cd ../frontend
npm test
npm run build
cd ..
docker compose config
git diff --check
```

Expected: all backend tests, frontend tests, build, Compose validation, and whitespace checks pass.

- [ ] **Step 2: Verify development lifecycle commands with real services**

```bash
./start.sh --stop
./start.sh
./start.sh --status
curl --fail --silent --show-error http://127.0.0.1:8300/api/health
curl --fail --silent --show-error http://127.0.0.1:3100/
./start.sh --restart
./start.sh --status
./start.sh --stop
./start.sh --status
```

Expected: start and restart report healthy services; health requests succeed; stop leaves all four services stopped without removing volumes. If an application port was occupied, use the actual URL printed by `start.sh` and recorded in `.pids/`.

- [ ] **Step 3: Verify full-container mode**

```bash
docker compose up --build -d
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8300/api/health
curl --fail --silent --show-error http://127.0.0.1:3100/
docker compose down
```

Expected: all four services become healthy, both HTTP requests succeed, and `down` preserves named volumes because `--volumes` is not used.

- [ ] **Step 4: Verify indexing and retrieval on real Qdrant**

Run with `DETERMINISTIC_MODEL_FALLBACK=true` in local `.env`, start development mode, then:

```bash
curl --fail --silent --show-error \
  -F 'file=@README.md;type=text/markdown' \
  http://127.0.0.1:8300/api/documents/upload
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -d '{"query":"Auto Reign startup","limit":3}' \
  http://127.0.0.1:8300/api/rag/search
```

Expected: upload returns `index_status` equal to `completed`, and search returns at least one document hit.

- [ ] **Step 5: Inspect final scope and commit any verification fixes**

```bash
git status --short
git diff --stat origin/main...HEAD
git log --oneline origin/main..HEAD
```

Expected: only the approved design, plan, storage/runtime implementation, tests, and documentation are present. If verification required fixes, commit them with an imperative message describing the behavior fixed.
