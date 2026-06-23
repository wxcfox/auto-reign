# Interview Chat Flow History Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the chat-first interview workbench so interview flow, history, settings, and library display match the requested ChatGPT/Wegent-style behavior.

**Architecture:** Keep the existing FastAPI, SQLAlchemy, Next.js, and SSE structure. Add narrow backend list/detail/stream-finish responses, then adapt the existing single chat workspace to load resumable sessions, auto-advance or auto-finish without manual next-question decisions, and expose history/settings through the app shell.

**Tech Stack:** FastAPI, SQLAlchemy 2, Pydantic, pytest, Next.js App Router, React Testing Library, Vitest, lucide-react, CSS variables.

---

### Task 1: Backend Interview History And Streamed Finish

**Files:**
- Modify: `backend/app/repositories/database.py`
- Modify: `backend/app/schemas/interviews.py`
- Modify: `backend/app/api/interviews.py`
- Modify: `backend/tests/test_interviews.py`

- [ ] **Step 1: Write failing backend tests**

Add tests asserting `/api/interview-sessions` returns newest sessions with config/turns, active sessions can be opened, completed sessions are marked non-resumable, and `/api/interview-sessions/{id}/finish/stream` emits `delta` plus `result`.

- [ ] **Step 2: Run focused backend tests and verify failure**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_interviews.py::test_list_interview_sessions_includes_history_context tests/test_interviews.py::test_stream_finish_returns_summary_delta_and_result -q`

Expected: FAIL because the list and stream finish endpoints do not exist.

- [ ] **Step 3: Implement repository, schema, and API support**

Add `InterviewSessionRepository.list_recent`, Pydantic list summary/detail response models, `GET /api/interview-sessions`, and `POST /api/interview-sessions/{id}/finish/stream`. The finish stream can emit the created report summary as a single `delta` before the `result` payload, preserving the existing deterministic finish behavior.

- [ ] **Step 4: Run focused backend tests**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_interviews.py::test_list_interview_sessions_includes_history_context tests/test_interviews.py::test_stream_finish_returns_summary_delta_and_result -q`

Expected: PASS.

### Task 2: Frontend API Types And Workspace Flow

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/InterviewWorkspace.tsx`
- Modify: `frontend/src/components/__tests__/InterviewWorkspace.test.tsx`

- [ ] **Step 1: Write failing frontend tests**

Add tests for automatic next question after a main answer when no follow-up is returned, automatic finish on the final round, streamed report summary display, loading message alignment class, and loading an active/completed session from `sessionId`.

- [ ] **Step 2: Run focused frontend tests and verify failure**

Run: `cd frontend && npm test -- InterviewWorkspace.test.tsx`

Expected: FAIL because session loading, stream finish, and auto-finalization are missing or incomplete.

- [ ] **Step 3: Implement API and component behavior**

Add `listInterviewSessions`, `getInterviewSession`, `finishInterviewStream`. Update `InterviewWorkspace` to accept an optional `sessionId`, load history detail, auto-advance when no follow-up exists, auto-finish the last completed turn, remove manual finish controls, render report summary as assistant output, and align loading rows with the chat thread width.

- [ ] **Step 4: Run focused frontend tests**

Run: `cd frontend && npm test -- InterviewWorkspace.test.tsx`

Expected: PASS.

### Task 3: Sidebar History, User Settings, And Dashboard Simplification

**Files:**
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/components/__tests__/AppShell.test.tsx`
- Modify: `frontend/src/app/page.tsx`
- Modify: `frontend/src/i18n/locales/en/common.json`
- Modify: `frontend/src/i18n/locales/zh-CN/common.json`
- Modify: `frontend/src/app/globals.css`

- [ ] **Step 1: Write failing AppShell tests**

Update tests to expect one `New interview` entry, `New learning`, no duplicated `Interview` primary item, a `History` group under `More`, disabled completed-history buttons, and one bottom user/settings button exposing language and dark mode controls.

- [ ] **Step 2: Run focused AppShell tests and verify failure**

Run: `cd frontend && npm test -- AppShell.test.tsx`

Expected: FAIL because the current shell still has duplicate interview/recent/settings buttons.

- [ ] **Step 3: Implement shell behavior**

Fetch interview history in the shell, render active sessions as links to `/interview?session=<id>`, render completed sessions as disabled rows, keep only `Workbench` under `More`, merge user/settings into one menu, add language and theme controls, and simplify the dashboard to compact stats.

- [ ] **Step 4: Run focused AppShell tests**

Run: `cd frontend && npm test -- AppShell.test.tsx`

Expected: PASS.

### Task 4: Library Original Filename Display

**Files:**
- Modify: `backend/app/api/workspace.py`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/app/library/page.tsx`
- Modify: `frontend/src/app/library/page.test.tsx`
- Modify: `backend/tests/test_workspace_api.py`

- [ ] **Step 1: Write failing tests**

Backend test asserts source artifact summaries include `display_name` from `source_filename`; frontend test asserts source rows show the original filename instead of the UUID-prefixed relative path.

- [ ] **Step 2: Verify failures**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_workspace_api.py::test_workspace_artifacts_include_source_display_name -q`

Run: `cd frontend && npm test -- page.test.tsx`

Expected: FAIL because summaries expose only `relative_path`.

- [ ] **Step 3: Implement display name support**

Add `display_name` to workspace artifact summaries, using `source_filename` for raw sources and the basename of `relative_path` for generated assets. Update the library UI to show display name as the row title and relative path as secondary metadata.

- [ ] **Step 4: Run focused tests**

Run the two focused commands from Step 2.

Expected: PASS.

### Task 5: Verification And PR

**Files:**
- Commit all modified files.

- [ ] **Step 1: Run backend checks**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest -v`

Run: `cd backend && uv run ruff check .`

- [ ] **Step 2: Run frontend checks**

Run: `cd frontend && npm test`

Run: `cd frontend && npm run build`

- [ ] **Step 3: Run repository checks**

Run: `git diff --check`

- [ ] **Step 4: Commit, push, create PR**

Commit with a concise message, push `codex/interview-chat-flow-history-polish`, and create a non-draft PR against `main`.
