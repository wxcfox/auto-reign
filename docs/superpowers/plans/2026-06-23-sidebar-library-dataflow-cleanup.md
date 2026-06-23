# Sidebar, Library, And Knowledge Data Flow Cleanup Plan

**Date:** 2026-06-23
**Status:** Completed
**Spec:** `docs/superpowers/specs/2026-06-22-filesystem-first-interview-workbench-design.md`
**Scope:** Narrow UI and API polish on top of the current filesystem-first workbench.

## Goals

- Add a collapsible left sidebar in the ChatGPT/Wegent style without changing routing.
- Redesign the library browser as a categorized document table with name, ownership, created time, updated time, and edit/delete actions.
- Preserve original source filenames in the library display.
- Add a user-triggered artifact delete path for library rows.
- Document the current knowledge-base ingestion, chunking, embedding, indexing, and retrieval flow.
- Clean stale README/front-end API references that no longer match the current workspace implementation.

## Phase 1: Workspace Artifact Metadata And Delete API

Files:

- `backend/app/api/workspace.py`
- `backend/app/services/artifact_service.py`
- `frontend/src/lib/types.ts`
- `frontend/src/lib/api.ts`
- `backend/tests/test_workspace_api.py`

Changes:

- Extend artifact summary responses with `owner`, `created_at`, and `updated_at`.
- Add an explicit `DELETE /api/workspace/artifacts/{artifact_id}` endpoint.
- Remove the artifact file and source sidecar when a user explicitly deletes a source artifact.
- Rebuild the projection after deletion so MySQL metadata matches the filesystem.
- Add backend tests for metadata and deletion behavior.

Verification:

- `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_workspace_api.py`

## Phase 2: Collapsible Sidebar And Library Table

Files:

- `frontend/src/components/AppShell.tsx`
- `frontend/src/app/library/page.tsx`
- `frontend/src/app/globals.css`
- `frontend/src/i18n/locales/en/library.json`
- `frontend/src/i18n/locales/zh-CN/library.json`
- `frontend/src/components/__tests__/AppShell.test.tsx`
- `frontend/src/app/library/page.test.tsx`

Changes:

- Add persisted sidebar collapsed state and icon-only collapsed navigation.
- Keep settings opening upward from the local user button.
- Replace the library file rows with a compact table/list pattern: name, owner/category, created time, updated time, actions.
- Add edit and delete action buttons with lucide icons.
- Keep category filtering and keyword search.
- Add frontend tests for sidebar collapse and library delete actions.

Verification:

- `cd frontend && npm test -- AppShell LibraryPage`
- `cd frontend && npm run build`

## Phase 3: Documentation And Stale Reference Cleanup

Files:

- `docs/knowledge-data-flow.md`
- `README.md`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/types.ts`

Changes:

- Add a Mermaid flowchart for the current knowledge-base data flow.
- Update README sections that still describe the old upload path, limited file support, and old smoke path.
- Remove unused frontend-only legacy `/api/documents` client types/functions now that the UI uses workspace artifacts.

Verification:

- `git diff --check`
- `docker compose config`
