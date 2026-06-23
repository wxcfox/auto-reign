# Documentation Map

This directory keeps durable engineering and product documentation only. Avoid
using it as a permanent archive for one-off implementation plans.

## Canonical Documents

- `../README.md`: currently runnable implementation, setup, configuration, and
  smoke-test instructions.
- `superpowers/specs/2026-06-22-filesystem-first-interview-workbench-design.md`:
  target product and architecture specification for the filesystem-first
  interview workbench.
- `knowledge-data-flow.md`: current knowledge-base ingestion, projection,
  chunking, embedding, indexing, and retrieval flow.

## Documentation Lifecycle

- Process plans under `docs/superpowers/plans/` are temporary working artifacts.
  Delete them when the implementation phase is complete.
- If a plan contains a lasting decision, move that decision into the relevant
  canonical document before deleting the plan.
- Do not duplicate the same behavior across multiple documents. Keep the fact in
  one canonical place and link to it from other docs.
- When code changes product behavior, update the matching documentation in the
  same PR.
