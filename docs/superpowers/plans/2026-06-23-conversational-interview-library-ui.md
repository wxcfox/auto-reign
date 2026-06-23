# Conversational Interview And Library UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Auto Reign closer to Wegent's chat and knowledge-base interaction model: natural-language interview start, input-adjacent model selection, settings-contained language selection, a real "new learning" chat path, and categorized library navigation.

**Architecture:** Keep the existing FastAPI, Next.js, MySQL, Qdrant, and file-workspace boundaries. Add one focused backend endpoint for learning-note ingestion that stores the original note as a source and creates a managed knowledge artifact; keep interview session streaming endpoints intact. Update the existing React components rather than introducing a new UI framework.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy sessions, existing workspace/artifact services, Next.js App Router, React state, Vitest, Testing Library, existing CSS variables and lucide-react icons.

---

## File Map

- Modify `backend/app/api/workspace.py`: add `POST /api/workspace/learning-notes` request/response models and route.
- Modify `backend/app/services/model_service.py`: add deterministic and provider-backed structured learning-note summarization.
- Add `backend/app/prompts/learning_note_summary.md`: concise structured summarization prompt for user learning notes.
- Modify `backend/tests/test_workspace_api.py`: cover learning-note endpoint persistence and knowledge artifact projection.
- Modify `backend/app/prompts/question_generation.md`: clarify that `extra_prompt` may contain natural-language company, role, and JD context.
- Modify `backend/app/services/model_service.py`: improve deterministic fallback question when target company/role are blank.
- Modify `frontend/src/lib/types.ts`: add learning-note response types if needed.
- Modify `frontend/src/lib/api.ts`: add `recordLearningNote`.
- Modify `frontend/src/components/AppShell.tsx`: add separate sidebar actions for New interview and New learning; move language out of sidebar footer.
- Modify `frontend/src/app/interview/page.tsx`: keep existing interview route.
- Add `frontend/src/app/learn/page.tsx`: new learning chat page.
- Add `frontend/src/components/LearningWorkspace.tsx`: chat-style learning note entry and saved summary message.
- Modify `frontend/src/components/InterviewWorkspace.tsx`: allow sending the initial composer text as interview context, remove visible target-company/JD setup, move model selection into a compact input-adjacent picker, put language and simple interview settings behind settings.
- Modify `frontend/src/app/library/page.tsx`: replace flat document grid with category sidebar + selected category file list.
- Modify `frontend/src/app/globals.css`: Wegent-style model picker, compact settings popover, learning workspace, and library category layout.
- Modify `frontend/src/i18n/locales/*/*.json`: add labels for new sidebar actions, learning page, simplified interview settings, and categorized library.
- Modify frontend tests under `frontend/src/components/__tests__/` and add focused page/component tests for the new behavior.

## Task 1: Learning Note Backend

**Files:**
- Modify: `backend/app/services/model_service.py`
- Add: `backend/app/prompts/learning_note_summary.md`
- Modify: `backend/app/api/workspace.py`
- Test: `backend/tests/test_workspace_api.py`

- [ ] **Step 1: Write failing backend test**

Add a test that posts a note to `/api/workspace/learning-notes` with deterministic fallback enabled, expects a saved source and a `knowledge` artifact, and verifies the knowledge artifact body contains both the original note and generated summary sections.

- [ ] **Step 2: Run test to verify RED**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_workspace_api.py::test_record_learning_note_creates_knowledge_artifact -q`

Expected: fail with 404 or missing endpoint.

- [ ] **Step 3: Implement minimal backend**

Add a `LearningNoteSummaryResult` model and `summarize_learning_note()` in `ModelService`. Add the workspace route that stores the note as a source, creates `knowledge/<slug>.md`, rebuilds projection, schedules index rebuild, and returns source/artifact IDs plus summary.

- [ ] **Step 4: Run focused backend tests**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_workspace_api.py -q`

Expected: all workspace API tests pass.

## Task 2: Natural-Language Interview Start

**Files:**
- Modify: `backend/app/prompts/question_generation.md`
- Modify: `backend/app/services/model_service.py`
- Modify: `frontend/src/components/InterviewWorkspace.tsx`
- Modify: `frontend/src/i18n/locales/en/interview.json`
- Modify: `frontend/src/i18n/locales/zh-CN/interview.json`
- Test: `frontend/src/components/__tests__/InterviewWorkspace.test.tsx`
- Test: `backend/tests/test_model_service.py`

- [ ] **Step 1: Write failing tests**

Add frontend tests that verify the start composer is enabled without target-company/role fields, sends the typed context through `extra_prompt`, preserves that context as a user message, and starts even with an empty prompt. Add a backend deterministic fallback test for blank target company/role.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd frontend && npm test -- InterviewWorkspace`

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_model_service.py::test_model_service_generates_generic_question_without_target_fields -q`

Expected: fail because current UI requires target fields and fallback question is awkward.

- [ ] **Step 3: Implement interview flow**

Remove required target-company/role gating from `canStart`. Treat the first composer submission as optional natural-language context and save it in `extra_prompt`; leave target company, role, and JD blank unless existing config already has them. Render the initial context as a user message in the transcript. Update prompt copy so the model infers company/role/JD from `extra_prompt` when provided.

- [ ] **Step 4: Run focused tests**

Run: `cd frontend && npm test -- InterviewWorkspace`

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_model_service.py -q`

Expected: focused tests pass.

## Task 3: Wegent-Style Input Controls And Settings

**Files:**
- Modify: `frontend/src/components/InterviewWorkspace.tsx`
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/i18n/locales/en/common.json`
- Modify: `frontend/src/i18n/locales/zh-CN/common.json`
- Test: `frontend/src/components/__tests__/AppShell.test.tsx`
- Test: `frontend/src/components/__tests__/InterviewWorkspace.test.tsx`

- [ ] **Step 1: Write failing tests**

Assert that sidebar renders New interview and New learning actions, language selector is not in the sidebar footer, interview settings include language, and the compact model picker sits in the composer controls.

- [ ] **Step 2: Run tests to verify RED**

Run: `cd frontend && npm test -- AppShell InterviewWorkspace`

Expected: fail with missing New learning/settings behavior.

- [ ] **Step 3: Implement UI controls**

Add compact popover-style model picker next to the send button. Move provider/model selection into that picker. Replace advanced target fields with a settings panel containing language, interview mode, and target rounds. Remove `LanguageSwitcher` from the sidebar footer and use it inside the interview settings panel.

- [ ] **Step 4: Run focused tests**

Run: `cd frontend && npm test -- AppShell InterviewWorkspace`

Expected: focused tests pass.

## Task 4: New Learning Chat Page

**Files:**
- Add: `frontend/src/app/learn/page.tsx`
- Add: `frontend/src/components/LearningWorkspace.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/i18n/setup.ts`
- Add: `frontend/src/i18n/locales/en/learning.json`
- Add: `frontend/src/i18n/locales/zh-CN/learning.json`
- Add: `frontend/src/components/__tests__/LearningWorkspace.test.tsx`

- [ ] **Step 1: Write failing frontend test**

Mock `recordLearningNote`, submit a learning note, and assert the page keeps the user note in the chat flow and renders the saved summary/artifact response.

- [ ] **Step 2: Run test to verify RED**

Run: `cd frontend && npm test -- LearningWorkspace`

Expected: fail because the page/component/API do not exist.

- [ ] **Step 3: Implement learning chat**

Create a minimal chat page with the same centered input-card style. Use `recordLearningNote` to save notes. Keep the user note and assistant saved-summary response visible.

- [ ] **Step 4: Run focused test**

Run: `cd frontend && npm test -- LearningWorkspace`

Expected: pass.

## Task 5: Categorized Library Layout

**Files:**
- Modify: `frontend/src/app/library/page.tsx`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/i18n/locales/en/library.json`
- Modify: `frontend/src/i18n/locales/zh-CN/library.json`
- Add: `frontend/src/components/__tests__/LibraryPage.test.tsx`

- [ ] **Step 1: Write failing frontend test**

Mock `getWorkspaceArtifacts`, render the library page, assert category navigation appears, selecting a category filters files, and artifact rows are shown in a Wegent-like list rather than only flat cards.

- [ ] **Step 2: Run test to verify RED**

Run: `cd frontend && npm test -- LibraryPage`

Expected: fail because category navigation does not exist.

- [ ] **Step 3: Implement categorized layout**

Group artifacts into Sources, Profile, Knowledge, Practice, and System categories. Render a left category list with counts, a top search/action row, and a right file list for the selected category.

- [ ] **Step 4: Run focused test**

Run: `cd frontend && npm test -- LibraryPage`

Expected: pass.

## Task 6: Full Verification And PR

**Files:**
- All modified files.

- [ ] **Step 1: Run full backend verification**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest -v`

Run: `cd backend && uv run ruff check .`

- [ ] **Step 2: Run full frontend verification**

Run: `cd frontend && npm test`

Run: `cd frontend && npm run build`

- [ ] **Step 3: Run repository checks**

Run: `docker compose config`

Run: `git diff --check`

- [ ] **Step 4: Smoke test local UI and endpoints**

Start backend/frontend with temporary data, confirm `/api/health`, `/api/workspace/learning-notes`, and `/interview`, `/learn`, `/library` are reachable.

- [ ] **Step 5: Commit, push, open ready PR**

Commit on `codex/conversational-interview-library-ui`, push to origin, and open a non-draft PR against `main`.

## Self-Review

- Spec coverage: covers language-in-settings, Wegent-like model picker placement, natural-language interview context, New interview/New learning sidebar actions, learning-note persistence, and categorized library layout.
- Placeholder scan: no `TBD`/`TODO` placeholders remain.
- Type consistency: frontend API names align around `recordLearningNote`; backend route names align around `learning-notes`; existing interview stream functions remain unchanged.
