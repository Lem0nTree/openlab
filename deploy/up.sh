#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)

"$script_dir/bootstrap-secrets.sh"
exec docker compose --env-file "$repo_root/.env" -f "$script_dir/compose.yml" up "$@"
