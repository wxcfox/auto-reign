#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required to inspect Alembic heads" >&2
  exit 2
fi

if [[ ! -d "$BACKEND_DIR/alembic" ]]; then
  echo "error: Alembic directory not found: $BACKEND_DIR/alembic" >&2
  exit 2
fi

if ! heads_output="$(cd -- "$BACKEND_DIR" && uv run alembic heads 2>&1)"; then
  echo "error: failed to inspect Alembic heads from $BACKEND_DIR" >&2
  printf '%s\n' "$heads_output" >&2
  exit 2
fi

head_lines="$(printf '%s\n' "$heads_output" | grep -E '[(]head[)]([[:space:]]|$)' || true)"
head_count="$(printf '%s\n' "$head_lines" | sed '/^[[:space:]]*$/d' | wc -l | tr -d '[:space:]')"

if [[ "$head_count" -ne 1 ]]; then
  echo "error: expected exactly one Alembic head, found $head_count" >&2
  if [[ -n "$heads_output" ]]; then
    printf '%s\n' "$heads_output" >&2
  fi
  exit 1
fi

echo "Alembic head check passed: $head_lines"
