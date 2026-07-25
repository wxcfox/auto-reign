#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
BASE_REF="${1:-${DOCS_IMPACT_BASE_REF:-}}"

if [[ -z "$BASE_REF" ]]; then
  echo "usage: scripts/check-docs-impact.sh <base-ref>" >&2
  exit 2
fi

if [[ "$BASE_REF" == "--root" ]]; then
  if ! changed_files="$(git -C "$REPO_ROOT" ls-tree -r --name-only HEAD)"; then
    echo "error: failed to calculate changed files from the empty repository to HEAD" >&2
    exit 2
  fi
else
  if ! git -C "$REPO_ROOT" rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null; then
    echo "error: base ref is not a valid commit: $BASE_REF" >&2
    exit 2
  fi

  if ! changed_files="$(git -C "$REPO_ROOT" diff \
    --no-renames \
    --name-only \
    --diff-filter=ACDMRT \
    "$BASE_REF"...HEAD)"; then
    echo "error: failed to calculate changed files from $BASE_REF to HEAD" >&2
    exit 2
  fi
fi

requires_platform=0
requires_knowledge=0
requires_deployment=0

while IFS= read -r path; do
  [[ -z "$path" ]] && continue

  case "$path" in
    backend/app/api/admin_users.py|\
    backend/app/api/agents.py|\
    backend/app/api/subtask_contexts.py|\
    backend/app/api/tasks.py|\
    backend/app/api/workspaces.py|\
    backend/app/api/ws/*|\
    backend/app/api/auth.py|\
    backend/app/api/dependencies.py|\
    backend/app/core/auth.py|\
    backend/app/core/password_policy.py|\
    backend/app/core/passwords.py|\
    backend/app/core/request_context.py|\
    backend/app/core/socketio.py|\
    backend/app/db/*|\
    backend/app/repositories/resource_repository.py|\
    backend/app/repositories/subtask_context_repository.py|\
    backend/app/repositories/task_repository.py|\
    backend/app/repositories/user_repository.py|\
    backend/app/schemas/admin_users.py|\
    backend/app/schemas/agents.py|\
    backend/app/schemas/auth.py|\
    backend/app/schemas/resources.py|\
    backend/app/schemas/socket_events.py|\
    backend/app/schemas/subtask_contexts.py|\
    backend/app/schemas/tasks.py|\
    backend/app/schemas/workspaces.py|\
    backend/app/services/admin_user_service.py|\
    backend/app/services/agent_home_*|\
    backend/app/services/agent_runtime.py|\
    backend/app/services/agent_service.py|\
    backend/app/services/chat_*|\
    backend/app/services/context_assembler.py|\
    backend/app/services/message_chain.py|\
    backend/app/services/react_loop.py|\
    backend/app/services/runtime_*|\
    backend/app/services/subtask_*|\
    backend/app/services/task_*|\
    backend/app/services/workspace_resource_service.py|\
    backend/app/prompts/platform/context_budget.md|\
    backend/app/storage/*|\
    backend/alembic/env.py|\
    backend/alembic/versions/*|\
    frontend/src/app/admin/users/*|\
    frontend/src/app/admin/agents/*|\
    frontend/src/app/admin/workspaces/*|\
    frontend/src/app/agents/*|\
    frontend/src/app/chat/*|\
    frontend/src/app/workspaces/*|\
    frontend/src/components/AdminUserManagementPage.tsx|\
    frontend/src/components/Agent*.tsx|\
    frontend/src/components/AuthGuard.tsx|\
    frontend/src/components/ChatMessage.tsx|\
    frontend/src/components/ChatWorkspace.tsx|\
    frontend/src/components/RoleGuard.tsx|\
    frontend/src/components/SubtaskContexts.tsx|\
    frontend/src/components/Workspace*.tsx|\
    frontend/src/components/chat/*|\
    frontend/src/contexts/SocketContext.tsx|\
    frontend/src/lib/auth.ts|\
    frontend/src/lib/socket-types.ts|\
    frontend/src/lib/task-events.ts|\
    frontend/src/lib/types.ts|\
    frontend/src/i18n/locales/*/agents.json|\
    frontend/src/i18n/locales/*/workspaces.json)
      requires_platform=1
      ;;
  esac

  case "$path" in
    backend/app/api/knowledge.py|\
    backend/app/api/knowledge_collections.py|\
    backend/app/repositories/knowledge_document_repository.py|\
    backend/app/repositories/vector_store.py|\
    backend/app/schemas/knowledge.py|\
    backend/app/schemas/knowledge_collections.py|\
    backend/app/services/knowledge_*|\
    backend/app/services/knowledge_retrievers/*|\
    backend/app/services/document_operation_coordinator.py|\
    backend/app/services/embedding_service.py|\
    backend/app/services/extraction_service.py|\
    backend/app/services/upload_validation_service.py|\
    backend/app/tools/knowledge.py|\
    backend/app/prompts/platform/knowledge_base.md|\
    frontend/src/app/admin/knowledge/*|\
    frontend/src/app/knowledge/*|\
    frontend/src/components/Knowledge*.tsx|\
    frontend/src/i18n/locales/*/knowledge.json)
      requires_knowledge=1
      ;;
  esac

  case "$path" in
    deploy/*|\
    .github/workflows/release.yml|\
    backend/Dockerfile|\
    frontend/Dockerfile|\
    docker-compose.yml|\
    .env.example|\
    start.sh|\
    reset-data.sh|\
    scripts/start.py|\
    scripts/reset_all_data.py|\
    scripts/audit_object_orphans.py)
      requires_deployment=1
      ;;
  esac
done <<< "$changed_files"

document_changed_and_exists() {
  local path="$1"
  grep -Fxq "$path" <<< "$changed_files" \
    && git -C "$REPO_ROOT" cat-file -e "HEAD:$path" >/dev/null 2>&1
}

missing_docs=()
if [[ "$requires_platform" -eq 1 ]] \
  && ! document_changed_and_exists "docs/architecture/platform.md"; then
  missing_docs+=("docs/architecture/platform.md")
fi
if [[ "$requires_knowledge" -eq 1 ]] \
  && ! document_changed_and_exists "docs/architecture/knowledge.md"; then
  missing_docs+=("docs/architecture/knowledge.md")
fi
if [[ "$requires_deployment" -eq 1 ]] \
  && ! document_changed_and_exists "docs/operations/production.md"; then
  missing_docs+=("docs/operations/production.md")
fi

if [[ "${#missing_docs[@]}" -gt 0 ]]; then
  echo "error: high-impact changes require the mapped authoritative document(s):" >&2
  printf '  - %s\n' "${missing_docs[@]}" >&2
  exit 1
fi

echo "Documentation impact check passed"
