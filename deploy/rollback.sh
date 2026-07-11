#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
source "$DEPLOY_DIR/lib.sh"

if [[ $# -ne 2 || "$2" != "--yes" ]]; then
  echo "Usage: $0 VERSION --yes" >&2
  echo "Rollback changes application images only. It does not downgrade the MySQL schema." >&2
  exit 2
fi

version="$(validate_version "$1")"
echo "Rolling back application images to $version without downgrading the database schema."
exec "$DEPLOY_DIR/deploy.sh" "$version" --skip-migration
