# Settings Toggle And Learning Language Polish

## Goal

Refine the merged ChatGPT-style UI so the sidebar user menu uses direct toggle
actions instead of dropdowns, and ensure learning-note summaries respect the
selected language in both streamed prompts and final chat rendering.

## Phase 1: Sidebar Settings Toggles

- Files:
  - `frontend/src/components/AppShell.tsx`
  - `frontend/src/i18n/locales/en/common.json`
  - `frontend/src/i18n/locales/zh-CN/common.json`
  - `frontend/src/app/globals.css`
  - `frontend/src/components/__tests__/AppShell.test.tsx`
- Behavior:
  - Replace the language dropdown and dark-mode checkbox inside the footer user
    menu with two plain menu buttons.
  - Button labels represent the state they switch to:
    - current Chinese -> `English`
    - current English -> `简体中文`
    - current light -> dark-mode label
    - current dark -> light-mode label
  - The footer menu opens upward from the local-user button.
- Tests:
  - Assert no combobox/checkbox is rendered in the footer menu.
  - Assert clicking language/theme buttons toggles the displayed target labels.

## Phase 2: Learning Summary Language Consistency

- Files:
  - `backend/app/prompts/learning_note_summary_stream.md`
  - `backend/tests/test_workspace_api.py`
  - `frontend/src/components/LearningWorkspace.tsx`
  - `frontend/src/components/__tests__/LearningWorkspace.test.tsx`
- Behavior:
  - The streaming prompt must explicitly require headings and prose in the
    requested language, while preserving technical terms.
  - The final assistant message should render normalized Markdown from the
    structured result using localized section headings, rather than keeping an
    English-shaped stream forever.
- Tests:
  - Backend stream prompt contains explicit Chinese and English section shapes.
  - Frontend Chinese learning flow ends with Chinese headings even if the stream
    sent English-shaped Markdown.

## Verification

- `cd frontend && npm test -- AppShell.test.tsx LearningWorkspace.test.tsx`
- `cd frontend && npm run build`
- `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_workspace_api.py -q`
- `git diff --check`

## Data Effects

No automatic data reset or destructive migration. Existing saved learning files
are not rewritten; the change affects newly generated learning-note summaries
and the final chat rendering for the active request.
