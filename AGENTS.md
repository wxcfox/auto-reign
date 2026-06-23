# Repository Guidelines

## Product Direction

Auto Reign is a local-first, single-user AI interview learning workbench. The
current codebase is a working v1 built with FastAPI, Next.js, MySQL, and Qdrant.
The canonical target design for the next development cycle is:

- `docs/superpowers/specs/2026-06-22-filesystem-first-interview-workbench-design.md`

Read that specification before changing product behavior, storage, ingestion,
interview flow, memory, retrieval, or the main UI. `README.md` documents the
currently runnable implementation; the target design intentionally supersedes
parts of the current behavior.

The target design does not require compatibility with existing MySQL records,
Qdrant points, or runtime files. Do not add dual-read, dual-write, data-copy, or
legacy prompt branches. Never delete local user data automatically; destructive
reset commands must remain explicit.

## Project Structure

- `backend/app/`: FastAPI application, APIs, services, repositories, schemas,
  database models, and prompts.
- `backend/alembic/`: MySQL schema migrations.
- `backend/tests/`: backend unit and integration tests.
- `frontend/src/`: Next.js application, components, i18n resources, and tests.
- `scripts/`: repository lifecycle tooling used by `start.sh`.
- `docs/superpowers/specs/`: approved product and architecture specifications.
- `docs/superpowers/plans/`: temporary implementation plans created from
  approved specs while a phase is actively being implemented.
- `data/`: local runtime data; it is not source code and must remain ignored.

Do not introduce a parallel `src/` tree at the repository root. Follow the
existing backend and frontend organization.

## Development Workflow

For the filesystem-first workbench redesign:

1. Inspect the current implementation and the canonical design.
2. Write a phased implementation plan under `docs/superpowers/plans/` before
   changing application code.
3. Map each task to exact files, tests, migration effects, and verification
   commands.
4. Implement one independently verifiable phase at a time.
5. Keep the application runnable and tests passing at each phase boundary.

Do not attempt the entire redesign as one unreviewable rewrite. Reuse existing
code where it fits the target boundaries, and delete obsolete code when its
replacement is complete.

Implementation plans are process artifacts, not durable product documentation.
Before a PR is ready, delete completed one-off plans or promote their lasting
decisions into `README.md`, `docs/README.md`, `docs/superpowers/specs/`, or a
focused topic document under `docs/`. Do not keep stale completed plans merely as
history.

When changing behavior, update the canonical documentation in the same PR:
`README.md` for runnable behavior, `docs/superpowers/specs/` for target
architecture, and focused `docs/*.md` files for current operational flows. Avoid
duplicating the same facts across multiple docs; prefer linking to the canonical
source.

## Canonical Commands

Start or manage the local stack from the repository root:

```sh
./start.sh
./start.sh --status
./start.sh --stop
./start.sh --restart
```

Run repository checks before committing:

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

Prefer these commands over ad hoc alternatives. Add focused tests first for
behavioral changes, then run the broader relevant suite.

## Coding Conventions

- Python targets 3.12+, uses type hints, Pydantic models, SQLAlchemy 2, Ruff,
  and pytest. Keep services focused and keep provider or persistence details
  behind explicit interfaces.
- TypeScript follows the existing Next.js and React patterns. Preserve i18n,
  loading, empty, and error states for user-facing changes.
- LLM calls return validated structured output. LLMs do not write files or
  databases directly; deterministic application code applies changes.
- Preserve original user sources and answers. Generated content, personal
  facts, and observed practice evidence must retain distinct provenance.
- Keep prompts concise, task-specific, language-aware, and resistant to prompt
  injection from uploaded content.
- Do not commit secrets, `.env`, dependency directories, runtime data, logs, or
  machine-specific configuration.

## Testing Expectations

Every behavioral change requires tests for expected behavior, edge cases, and
failure paths. Use deterministic model and vector test doubles unless a test is
explicitly an integration check. Storage changes must cover MySQL migrations,
filesystem failure behavior, and Qdrant recovery. Frontend changes must cover
the primary user flow, not only isolated presentation components.

Pull requests should state scope, design phase, test evidence, data reset
effects, and screenshots for visible UI changes.
