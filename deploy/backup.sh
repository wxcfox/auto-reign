#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
source "$DEPLOY_DIR/lib.sh"

require_command docker
require_command gzip
require_command tar
load_deploy_env
require_deploy_paths

umask 077
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
state_dir="${AUTO_REIGN_STATE_DIR:-/srv/auto-reign}"
if [[ -f "$state_dir/deployed-version" ]]; then
  version="$(<"$state_dir/deployed-version")"
else
  version="${AUTO_REIGN_VERSION:-not-deployed}"
fi
backup_dir="$AUTO_REIGN_BACKUP_DIR/${timestamp}-${version}.incomplete"
final_dir="${backup_dir%.incomplete}"

mkdir -p "$backup_dir"

mysql_container="$(compose ps --status running --quiet mysql 2>/dev/null || true)"
if [[ -n "$mysql_container" ]]; then
  compose exec -T mysql sh -c \
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump --user=root --single-transaction --routines --events --databases "$MYSQL_DATABASE"' \
    | gzip -9 > "$backup_dir/mysql.sql.gz"
  mysql_status="included"
elif [[ -d "$AUTO_REIGN_MYSQL_DIR" ]] && find "$AUTO_REIGN_MYSQL_DIR" -mindepth 1 -print -quit | grep -q .; then
  die "MySQL data exists but the mysql container is not running; start it before deploying so a consistent backup can be created"
else
  mysql_status="not-initialized"
fi

if [[ -d "$AUTO_REIGN_DATA_DIR" ]]; then
  tar -C "$AUTO_REIGN_DATA_DIR" -czf "$backup_dir/workspace.tar.gz" .
  workspace_status="included"
else
  workspace_status="not-initialized"
fi

cat > "$backup_dir/metadata.txt" <<EOF
created_at=$timestamp
application_version=$version
mysql=$mysql_status
workspace=$workspace_status
qdrant=rebuildable-not-included
elasticsearch=rebuildable-not-included
EOF

if command -v sha256sum >/dev/null 2>&1; then
  (
    cd "$backup_dir"
    sha256sum ./* > SHA256SUMS
  )
fi

mv "$backup_dir" "$final_dir"
echo "Backup created: $final_dir"
