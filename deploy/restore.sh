#!/usr/bin/env sh
set -eu

archive=${1:?Usage: restore.sh path/to/openlab-backup.tar.gz}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
tar -C "$tmp" -xzf "$archive"
test -f "$tmp/manifest.json"
docker compose -f deploy/compose.yml exec -T postgres pg_restore -U "${POSTGRES_USER:-openlab}" -d "${POSTGRES_DB:-openlab}" --clean --if-exists < "$tmp/database.dump"
docker compose -f deploy/compose.yml cp "$tmp/attachments/." openlab-server:/var/lib/openlab
echo "Restore complete. Run health checks before accepting writes."

