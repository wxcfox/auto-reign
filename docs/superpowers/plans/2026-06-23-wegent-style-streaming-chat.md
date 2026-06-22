# Wegent Style Streaming Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the interview page visually match a restrained ChatGPT/Wegent-style chat product and stream model output incrementally before the final structured result is saved.

**Architecture:** Keep the current FastAPI + Next.js boundaries. Add small SSE endpoints beside the existing JSON endpoints so existing clients and tests remain valid, use the model service to stream provider chunks while accumulating full content, then validate with the existing Pydantic schemas before mutating interview rows. On the frontend, add a focused SSE reader and render temporary streaming text in the current assistant message until the final `result` event reconciles the structured turn state.

**Tech Stack:** FastAPI `StreamingResponse`, OpenAI-compatible chat completions with `stream=True`, Pydantic v2, Next.js/React, Vitest/Testing Library, existing CSS and lucide icons.

---

### Task 1: Backend SSE Contract and Streaming Model Support

**Files:**
- Modify: `backend/tests/test_model_service.py`
- Modify: `backend/app/services/model_service.py`

- [ ] **Step 1: Write failing model streaming tests**

Add tests that prove `ModelService` can emit provider chunks and that deterministic fallback emits more than one chunk.

```python
def test_model_service_streams_provider_chunks(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        openai_api_key="provider-secret",
        deterministic_model_fallback=False,
    )

    class FakeStreamCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return [
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hel"))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))]),
            ]

    completions = FakeStreamCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service = ModelService(settings=settings, client_factory=lambda **_kwargs: client)

    chunks = list(
        service.stream_chat(
            "question_generation.md",
            {"target_role": "Backend Engineer"},
            "openai",
            "gpt-4.1-mini",
        )
    )

    assert chunks == ["Hel", "lo"]
    assert completions.calls[0]["stream"] is True
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_model_service.py::test_model_service_streams_provider_chunks -q`

Expected: FAIL because `ModelService.stream_chat` does not exist.

- [ ] **Step 3: Implement model streaming**

Add `stream_chat`, `_stream_chat`, and a small fallback chunker to `ModelService`. Keep `_structured_chat` as the final validation path for non-streaming callers. Provider errors must still map to `provider_call_failed` without leaking secrets.

- [ ] **Step 4: Run model service tests**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_model_service.py -q`

Expected: PASS.

### Task 2: Streaming Interview Endpoints

**Files:**
- Modify: `backend/tests/test_interviews.py`
- Modify: `backend/app/schemas/interviews.py`
- Modify: `backend/app/services/interview_service.py`
- Modify: `backend/app/api/interviews.py`

- [ ] **Step 1: Write failing API tests**

Add tests for `POST /api/interview-sessions/stream`, `POST /api/interview-sessions/{id}/answer/stream`, `POST /api/interview-sessions/{id}/follow-up-answer/stream`, and `POST /api/interview-sessions/{id}/next-question/stream`.

The answer stream test must assert:

```python
response = client.post(
    f"/api/interview-sessions/{session_id}/answer/stream",
    json={"answer": "I would design clear service boundaries."},
)
assert response.status_code == 200
assert response.headers["content-type"].startswith("text/event-stream")
body = response.text
assert "event: delta" in body
assert "event: result" in body
assert '"feedback"' in body
assert client.post(
    f"/api/interview-sessions/{session_id}/answer",
    json={"answer": "duplicate"},
).status_code == 409
```

- [ ] **Step 2: Run the focused API test and confirm it fails**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_interviews.py::test_stream_answer_feedback_returns_delta_and_result -q`

Expected: FAIL with 404 for the new endpoint.

- [ ] **Step 3: Implement SSE helpers and service streaming methods**

Use `StreamingResponse` with events formatted as:

```text
event: delta
data: {"text":"..."}

event: result
data: {"feedback":"...","missing_points":[],"follow_up_question":"...","weaknesses":[],"review_suggestions":[]}

event: error
data: {"message":"...","code":"provider_call_failed"}
```

Add service methods that:
- validate session state before streaming,
- set user answer immediately so the UI can show it,
- stream chunks from `ModelService`,
- accumulate the full model output,
- validate the final JSON into `AnswerEvaluationResult` or the next question string,
- write final fields to the current/new turn and flush.

- [ ] **Step 4: Run focused interview tests**

Run: `cd backend && env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_interviews.py -q`

Expected: PASS.

### Task 3: Frontend SSE Reader and Streaming State

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/components/__tests__/InterviewWorkspace.test.tsx`
- Modify: `frontend/src/components/InterviewWorkspace.tsx`

- [ ] **Step 1: Write failing frontend streaming test**

Mock `submitAnswerStream` to call `onDelta("First ")`, then `onDelta("chunk")`, then resolve the final feedback. Assert that the temporary assistant message appears before the final result and is replaced by structured feedback.

- [ ] **Step 2: Run the focused frontend test and confirm it fails**

Run: `cd frontend && npm test -- InterviewWorkspace`

Expected: FAIL because the streaming API function and UI state do not exist.

- [ ] **Step 3: Implement API stream reader**

Add a small fetch-based SSE reader that:
- posts JSON to the backend stream endpoint,
- decodes `event:` and `data:` frames,
- calls `onDelta(text)` for `delta`,
- returns typed payload for `result`,
- throws an `Error` for `error` or non-2xx responses.

- [ ] **Step 4: Render streaming text in the current assistant message**

Add a `streamingDraft` state with `{ turnId, kind, text }`. While the stream is active, render it in the same chat flow where final feedback/question will appear. Final `result` applies existing turn updates and clears the draft.

- [ ] **Step 4a: Remove the visible next-question control**

The frontend must not expose a "Next question" button. After the user submits a follow-up answer, call `nextQuestionStream` automatically when the session is still active and the target round count has not been reached. The next question appears as a normal assistant message in the existing thread.

- [ ] **Step 5: Run frontend tests**

Run: `cd frontend && npm test -- InterviewWorkspace`

Expected: PASS.

### Task 4: Wegent/ChatGPT-Style Visual Tightening

**Files:**
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/components/InterviewWorkspace.tsx`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/components/__tests__/AppShell.test.tsx`
- Modify: `frontend/src/components/__tests__/InterviewWorkspace.test.tsx`

- [ ] **Step 1: Write presentation-oriented assertions**

Keep tests behavioral: assert the sidebar exposes primary entries, less-used entries remain under More, model selector is visible in the composer, empty state is centered, and the retry/error state remains visible.

- [ ] **Step 2: Tighten shell and chat markup**

Remove heavy header/eyebrow treatment from the interview page, reduce brand/avatar weight, keep the left sidebar fixed on desktop and collapsible/compact on mobile, and put the model selector in a quiet top/composer control.

- [ ] **Step 3: Rewrite chat CSS to match Wegent-like proportions**

Use:
- `--background: #ffffff`, `--sidebar: #f9f9f9`, `--surface-muted: #f3f4f6`,
- sidebar width near 244px,
- chat content max width `820px`,
- input max width `820px`,
- message body line height around `1.7`,
- user messages as subtle rounded grey bubbles,
- assistant messages as unframed document flow,
- sticky/fixed bottom input with top fade on transcript.

- [ ] **Step 4: Run frontend tests and build**

Run:
```sh
cd frontend
npm test
npm run build
```

Expected: PASS.

### Task 5: Full Verification and PR

**Files:**
- Review all changed files.

- [ ] **Step 1: Run backend verification**

Run:
```sh
cd backend
env PYTHONDONTWRITEBYTECODE=1 uv run pytest -v
uv run ruff check .
```

Expected: PASS.

- [ ] **Step 2: Run frontend verification**

Run:
```sh
cd frontend
npm test
npm run build
```

Expected: PASS.

- [ ] **Step 3: Run repository verification**

Run:
```sh
docker compose config
git diff --check
```

Expected: PASS. Do not copy secrets from `docker compose config` into the final response.

- [ ] **Step 4: Commit, push, and open PR**

Commit only the scoped changes, push branch `codex/wegent-style-streaming-chat`, and open a PR to `main` with scope, streaming protocol, test evidence, and UI notes.
