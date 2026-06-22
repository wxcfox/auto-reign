# Filesystem-First Interview Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans` and
> implement each behavioral task with test-first red/green verification.

**Goal:** Replace the v1 document/memory/report assumptions with the approved
filesystem-first learning loop: upload real material, practise interviews, and let
the system maintain concise, evidence-backed learning assets automatically.

**Architecture:** `data/workspace/` is the durable source of truth. MySQL stores
active sessions, persistent jobs, and a rebuildable artifact projection. Qdrant is
a rebuildable retrieval index. LLMs return validated proposals; deterministic
services own files, database state, and vectors.

**Product invariants:**

- The primary user path is upload -> interview -> review. There is no parallel
  "daily practice" flow and no user-managed document roles, tags, chunks, trust
  levels, or indexes.
- Original uploads and interview answers are immutable evidence.
- Uploaded notes personalize context but never become correctness authority.
- Only archived practice evidence can change mastery.
- Generated content follows `session language -> workspace language -> zh-CN`.
- User-facing plans contain at most three priorities.
- `practice` is append-only, `mastery` supports scoped annotations/preferences,
  and source/extracted files are read-only.
- Damaged metadata never causes body loss or silently promotes content to evidence.
  Unresolved recovery artifacts remain excluded from retrieval and mastery.
- Existing v1 data is not migrated or dual-read. Destructive reset is explicit.

---

## Phase 1: Workspace Foundation

**Outcome:** The existing application remains runnable while the new filesystem,
artifact projection, recovery rules, and workspace index are added alongside v1.

### Task 1. Workspace schema and models

**Files:**

- Create `backend/app/schemas/workspace.py`
- Extend `backend/app/db/models.py`
- Create `backend/alembic/versions/20260622_0004_add_workspace_tables.py`
- Test in `backend/tests/test_workspace_models.py`

Add a singleton `workspace_settings` row, `artifacts`, and `processing_jobs`.
Artifact kinds and provenance are closed literals. `processing_status` includes
`needs_recovery`; index status includes `stale`. The migration is additive and
chains from `20260622_0003`.

### Task 2. Workspace and artifact services

**Files:**

- Create `backend/app/services/workspace_service.py`
- Create `backend/app/services/artifact_service.py`
- Test in `backend/tests/test_workspace_service.py`
- Test in `backend/tests/test_artifact_service.py`

Create the fixed directory tree and `workspace.md`. Restrict paths using resolved
containment, including symlink-parent escape checks. Parse/serialize front matter,
update known Markdown sections without rewriting unknown sections, atomically write
files, keep the latest 20 revisions by timestamp, and preserve raw source bytes with
JSON sidecars.

### Task 3. Projection scan and safe recovery

**Files:**

- Create `backend/app/repositories/artifact_repository.py`
- Extend `backend/app/services/workspace_service.py`
- Test in `backend/tests/test_workspace_projection.py`

Scan sidecars and managed Markdown to rebuild MySQL. Missing files remove their
projection and dependent jobs. Invalid/missing front matter preserves a revision and
body. Existing identity is restored by relative path when possible. Unmatched files
are marked durably in front matter as recovery-required, `edited_by=user`, and are
excluded from retrieval/mastery until resolved. Repeated rebuilds retain that state.

### Task 4. Rebuildable workspace index

**Files:**

- Extend `backend/app/repositories/vector_store.py`
- Extend `backend/app/repositories/qdrant_store.py`
- Create `backend/app/repositories/workspace_settings_repository.py`
- Create `backend/app/services/index_service.py`
- Test in `backend/tests/test_index_service.py`

Index raw Markdown/TXT sources, extracted text, knowledge, and verified practice.
Never index binary sources, reports, plan, mastery, archive, revisions, or recovery
artifacts. Full rebuild writes a versioned collection, then atomically commits the
active pointer and artifact statuses. Startup/rebuild sweeps non-active versioned
collections best-effort. A failed build leaves the old index active.

### Task 5. Operation authorization, reset, and startup

**Files:**

- Create `backend/app/core/artifact_permissions.py`
- Create `backend/app/db/migration_guards.py`
- Create `backend/scripts/reset_data.py`
- Create `backend/app/api/workspace.py`
- Extend `backend/app/main.py`, `backend/app/api/health.py`
- Test in `backend/tests/test_artifact_permissions.py`
- Test in `backend/tests/test_reset_data.py`
- Test in `backend/tests/test_workspace_api.py`

Authorize editing per operation, not with a single boolean. Reset backs up the whole
data directory, reflects and drops the actual database schema, deletes v1 and all
workspace Qdrant collections, reapplies migrations, and initializes a clean
workspace. Startup initializes the workspace and performs non-blocking orphan cleanup.

**Phase verification:**

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

---

## Phase 2: Automatic Material Organization

**Outcome:** Uploads return after immutable source storage; background processing
automatically extracts, classifies, organizes, merges, and indexes material.

### Task 1. Upload and extraction

Create `IngestionService`, `ExtractionService`, artifact API schemas/routes, and
tests for multi-file MD/TXT/PDF/DOCX upload, hash deduplication, filename safety,
20 MiB per-file and 1 GiB workspace limits, 1,000,000 analyzed/indexed characters,
unextractable PDFs, and source immutability.

### Task 2. Persistent jobs

Create job repository/service and an in-process worker. Test idempotency keys,
same-artifact ordering, bounded exponential retry, startup recovery, terminal safe
errors, and Qdrant-down behavior that completes files but leaves indexing stale.

### Task 3. Structured LLM ingestion

Add Pydantic output contracts and prompts for material routing, candidate/target
facts, conflicts, and concise knowledge cards. Test prompt-injection isolation,
source citations, no fabricated metrics, minimal-input/minimal-output behavior,
topic merge with revision, and preservation of the user's original understanding.

### Task 4. Artifact library and scoped editor

Replace v1 document/memory/report APIs and frontend pages atomically. Test reading,
allowed operations, optimistic revision conflicts, automatic reprocessing/indexing,
processing status, retry, and a UI with no tags/weakness/index maintenance controls.
Drop non-empty v1 tables only after an explicit reset guard.

---

## Phase 3: Targeted Interview and Learning Evidence

**Outcome:** One-click recommended interviews use target/profile/state plus diverse
retrieval; finishing a session creates durable evidence and updates learning state.

### Task 1. Retrieval and recommended configuration

Read target/candidate/mastery/plan directly and retrieve diverse source/knowledge/
practice chunks with citations. Test missing-state behavior, source diversity,
sourced-project-only questions, and recommended defaults with optional advanced
overrides.

### Task 2. Structured interview loop

Replace v1 question/evaluation/follow-up prompts and contracts. Test concise feedback
split into accurate/error/expression, one high-value follow-up, non-repetition, and
language resolution across real providers and deterministic fallback.

### Task 3. Practice, mastery, and plan

Archive every ended session with a valid answer before deriving anything else.
Preserve answers verbatim. Aggregate mastery only from verified practice; require two
sessions for fluent status. Recompute at most three evidence-linked tasks while
respecting explicit user priority preferences.

### Task 4. Finish pipeline and review artifacts

Run the idempotent sequence practice -> mastery -> knowledge follow-ups -> plan ->
short report -> projection/index. Test partial failures retain practice and display
"organizing" rather than asking for answer resubmission. Reports never feed facts or
mastery. Chinese sessions must produce Chinese questions, feedback, practice,
mastery, plan, and report files.

---

## Phase 4: Minimal Experience and Recovery

**Outcome:** The interface exposes only the core learning loop while diagnostics and
rebuild tools remain secondary.

### Task 1. Homepage, library, interview, and review convergence

Homepage shows target summary, one interview CTA, quick upload, latest progress, and
at most three priorities. Library is read-first with scoped editing. Interview starts
with recommended settings and hides advanced controls. Review prioritizes latest
performance and next actions with clickable source/evidence links.

### Task 2. Diagnostics and rebuild

Add settings-only health, provider, processing, projection rebuild, and index rebuild
actions. Do not expose database/vector terminology in the normal flow.

### Task 3. End-to-end acceptance

Automate the canonical scenario: reset; upload PDF resume, JD, and a short note;
verify concise organized files; complete a targeted Chinese interview; verify
practice/mastery/plan/report; edit knowledge and auto-reindex; delete MySQL projection
and Qdrant then rebuild successfully from workspace.

**Final verification:** Run all backend/frontend checks, start the Docker stack, and
walk the canonical browser flow on desktop and mobile before opening the PR.
