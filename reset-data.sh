#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if command -v uv >/dev/null 2>&1; then
  exec uv run python scripts/reset_all_data.py "$@"
fi

exec python3 scripts/reset_all_data.py "$@"
