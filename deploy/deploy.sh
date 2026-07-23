#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
source "$DEPLOY_DIR/lib.sh"

usage() {
  echo "Usage: $0 VERSION [--skip-migration]"
}

[[ $# -ge 1 ]] || { usage >&2; exit 2; }
version="$(validate_version "$1")"
shift
skip_migration=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-migration)
      skip_migration=true
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
  shift
done

require_command curl
require_command docker
require_command flock
load_deploy_env
require_deploy_paths
export AUTO_REIGN_VERSION="$version"

mkdir -p "$AUTO_REIGN_DATA_DIR" "$AUTO_REIGN_BACKUP_DIR"
lock_file="${AUTO_REIGN_STATE_DIR:-/srv/auto-reign}/deploy.lock"
mkdir -p "$(dirname "$lock_file")"
exec 9>"$lock_file"
flock -n 9 || die "another Auto Reign deployment is running"

compose config --quiet

echo "Creating pre-deployment backup..."
AUTO_REIGN_VERSION="$version" AUTO_REIGN_ENV_FILE="$ENV_FILE" "$DEPLOY_DIR/backup.sh"

echo "Pulling Auto Reign $version images..."
compose pull redis mysql qdrant elasticsearch backend frontend migrate

echo "Starting storage services..."
compose up -d redis mysql qdrant elasticsearch

if [[ "$skip_migration" == false ]]; then
  echo "Applying database migrations..."
  compose run --rm migrate
else
  echo "Skipping database migration for application-only rollback."
fi

echo "Updating application services..."
compose up -d --remove-orphans backend frontend

echo "Waiting for application health checks..."
healthy=false
for _ in $(seq 1 60); do
  if compose exec -T backend python3 -c \
      'import json,sys,urllib.request; body=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3)); sys.exit(0 if body.get("status") == "ok" and body.get("version") == sys.argv[1] else 1)' \
      "$version" >/dev/null 2>&1 \
    && compose exec -T frontend node -e \
      'fetch("http://127.0.0.1:3000").then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))' \
      >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep 2
done

[[ "$healthy" == true ]] || die "application did not become healthy; inspect: docker compose -f $COMPOSE_FILE logs"

if [[ -n "${DEPLOY_HEALTHCHECK_URL:-}" ]]; then
  curl --fail --silent --show-error --max-time 15 \
    "${DEPLOY_HEALTHCHECK_URL%/}/api/health" >/dev/null \
    || die "external health check failed: ${DEPLOY_HEALTHCHECK_URL%/}/api/health"
fi

state_dir="${AUTO_REIGN_STATE_DIR:-/srv/auto-reign}"
mkdir -p "$state_dir"
printf '%s\n' "$version" > "$state_dir/deployed-version"
echo "Auto Reign $version deployed successfully."
