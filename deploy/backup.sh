#!/usr/bin/env sh
set -eu

# Run inside the Compose project directory. The archive intentionally excludes
# runtime secrets; keep those in your password manager or Compose secret store.
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="${1:-./openlab-backup-${stamp}.tar.gz}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

docker compose -f deploy/compose.yml exec -T postgres pg_dump -U "${POSTGRES_USER:-openlab}" -d "${POSTGRES_DB:-openlab}" -Fc > "$tmp/database.dump"
docker compose -f deploy/compose.yml cp openlab-server:/var/lib/openlab "$tmp/attachments"
printf '{"format":"openlab-backup","version":1,"created_at":"%s"}\n' "$(date -u +%FT%TZ)" > "$tmp/manifest.json"
tar -C "$tmp" -czf "$out" database.dump attachments manifest.json
echo "Created $out"

