# Claude Code Instructions

## Required Context

Before implementing the next Auto Reign development cycle, read these files in
order:

1. `AGENTS.md`
2. `README.md`
3. `docs/superpowers/specs/2026-06-22-filesystem-first-interview-workbench-design.md`
4. The current backend, frontend, Alembic migrations, startup scripts, and tests
   relevant to the phase being planned.

`README.md` describes the currently runnable v1. The filesystem-first design is
the canonical target behavior and intentionally replaces the v1 document,
memory, retrieval, and review assumptions.

## Implementation Gate

Do not start by implementing the entire design. First create a detailed,
phased plan under `docs/superpowers/plans/`. The plan is a temporary working
artifact for the current phase, not durable project documentation. The plan
must:

- follow the four phases in the canonical design;
- name every file to create, modify, rename, or delete;
- define exact Pydantic contracts, database columns, API requests/responses,
  background job transitions, and filesystem update rules;
- include a failing test before each behavioral implementation task;
- list focused and phase-level verification commands;
- identify the explicit local data reset required by incompatible schema or
  workspace changes;
- keep the application runnable after every phase.

Implement only after the plan is coherent and internally reviewed. Work through
the plan task by task, verify each task, and make small intentional commits.
Before opening or updating a PR, delete completed one-off plans or promote the
lasting decisions into the canonical docs. Do not leave completed task plans in
the repository as historical clutter.

## Non-Negotiable Product Rules

- The user spends time uploading real material and practicing answers, not
  maintaining tags, document roles, trust levels, chunks, or indexes.
- Original sources and answers are immutable evidence. Corrections and generated
  material are separate artifacts.
- Uploaded notes personalize questions but are not authoritative correctness
  references.
- Only observed interview answers can change mastery state.
- Files are the durable learning assets. MySQL stores operational state and
  rebuildable projections; Qdrant is a rebuildable retrieval index.
- LLMs return validated structured proposals. Application code owns filesystem,
  database, and vector mutations.
- User-visible Markdown is editable according to the artifact-specific rules in
  the design and is reprocessed automatically.
- The active learning plan contains no more than three priorities.
- Do not preserve v1 behavior through compatibility branches when it conflicts
  with the target design.
- Never silently delete existing local data.

## Engineering Discipline

- Prefer the simplest implementation that satisfies the approved design.
- State assumptions in the implementation plan instead of making hidden choices.
- Keep changes scoped to the current task and remove only code made obsolete by
  that task.
- Use test-driven development for behavior changes.
- Run the canonical checks in `AGENTS.md` before declaring a phase complete.
- Do not claim completion based on code inspection alone; report fresh command
  output and any checks that could not run.
