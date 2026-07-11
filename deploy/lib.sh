#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${AUTO_REIGN_COMPOSE_FILE:-$DEPLOY_DIR/compose.prod.yml}"
ENV_FILE="${AUTO_REIGN_ENV_FILE:-/etc/auto-reign/auto-reign.env}"

die() {
  echo "error: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required"
}

load_deploy_env() {
  [[ -f "$ENV_FILE" ]] || die "production environment file not found: $ENV_FILE"
  set -a
  # The production environment file is administrator-controlled and must contain shell-safe values.
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  export AUTO_REIGN_ENV_FILE="$ENV_FILE"
}

require_deploy_paths() {
  : "${AUTO_REIGN_DATA_DIR:?Set AUTO_REIGN_DATA_DIR in $ENV_FILE}"
  : "${AUTO_REIGN_MYSQL_DIR:?Set AUTO_REIGN_MYSQL_DIR in $ENV_FILE}"
  : "${AUTO_REIGN_QDRANT_DIR:?Set AUTO_REIGN_QDRANT_DIR in $ENV_FILE}"
  : "${AUTO_REIGN_BACKUP_DIR:?Set AUTO_REIGN_BACKUP_DIR in $ENV_FILE}"
}

compose() {
  docker compose \
    --project-name auto-reign-production \
    --env-file "$ENV_FILE" \
    --file "$COMPOSE_FILE" \
    "$@"
}

validate_version() {
  local version="${1#v}"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must match MAJOR.MINOR.PATCH"
  printf '%s\n' "$version"
}
