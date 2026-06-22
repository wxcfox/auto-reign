# ChatGPT-Style Interview UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the Auto Reign interview page into a restrained ChatGPT-like chat workspace while preserving the current stack and keeping the app runnable.

**Architecture:** Keep the existing Next.js app shell and interview APIs, but reshape the frontend state around a full turn history instead of a single current turn. The shell owns the fixed left navigation and secondary items; `InterviewWorkspace` owns the centered chat transcript, sticky composer, advanced settings drawer, loading/streaming states, and mobile behavior. Backend token streaming is not implemented in this phase; the UI exposes streaming-style progressive rendering for returned model text, while a future backend phase can replace the transport with SSE.

**Tech Stack:** Next.js 16, React 19, TypeScript, CSS modules via global CSS, lucide-react icons, Vitest and Testing Library.

---

## Phase 1: Chat Layout and State

**Files:**
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/components/InterviewWorkspace.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/i18n/locales/en/common.json`
- Modify: `frontend/src/i18n/locales/zh-CN/common.json`
- Modify: `frontend/src/i18n/locales/en/interview.json`
- Modify: `frontend/src/i18n/locales/zh-CN/interview.json`
- Test: `frontend/src/components/__tests__/InterviewWorkspace.test.tsx`

Tasks:

- [ ] Add tests that verify the shell exposes a primary left sidebar, a "New interview" control, and a "More" area for secondary navigation.
- [ ] Add tests that verify `InterviewWorkspace` renders a centered empty chat state with a model selector and sticky composer.
- [ ] Add tests that simulate a started session, answering, and advancing to the next question while keeping the first question and feedback visible in the transcript.
- [ ] Add API typing for fetching session detail so the frontend can refresh all persisted turns when needed.
- [ ] Refactor `InterviewWorkspace` state from `turn` plus one feedback object to `turns[]`, `currentTurnId`, and composer state.
- [ ] Render questions, user answers, feedback, follow-up prompts, follow-up answers, and follow-up feedback as separate readable chat messages.
- [ ] Move company, role, JD, mode, rounds, and provider controls into a compact advanced settings panel; keep model selection visible in the composer header.
- [ ] Replace the current two-column interview layout CSS with a centered chat transcript, sticky bottom composer, large rounded input, and responsive mobile sidebar behavior.
- [ ] Preserve existing error handling, finish/report behavior, and i18n strings.

Verification:

- [ ] Run `cd frontend && npm test -- InterviewWorkspace.test.tsx`.
- [ ] Run `cd frontend && npm test`.
- [ ] Run `cd frontend && npm run build`.

## Phase 2: Real Streaming Transport

This phase is intentionally deferred because it changes backend model invocation and response contracts.

Future files:
- Modify: `backend/app/services/model_service.py`
- Modify: `backend/app/services/interview_service.py`
- Modify: `backend/app/api/interviews.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/InterviewWorkspace.tsx`
- Test: backend model/interview API streaming tests and frontend stream reader tests

Future tasks:

- [ ] Add model-service streaming helpers for provider text deltas.
- [ ] Add SSE or newline-delimited JSON endpoints for question generation and answer feedback.
- [ ] Persist the final validated structured output only after the stream completes.
- [ ] Update the frontend to consume stream events directly instead of progressive-rendering completed JSON responses.

## Scope Guard

This plan does not change storage schema, ingestion, retrieval ranking, interview prompts, or report generation. It keeps the current API-compatible interview flow while fixing the product experience problem that prior rounds disappear from view.
