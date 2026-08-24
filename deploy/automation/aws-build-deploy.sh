#!/usr/bin/env bash
set -Eeuo pipefail

sha=${1:-}
pi_host=${OPENLAB_PI_HOST:-openlab-pi}
pi_root=${OPENLAB_PI_ROOT:-/home/pi/openlab}
min_free_kb=${OPENLAB_MIN_FREE_KB:-2097152}

if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Expected a full lowercase Git commit SHA." >&2
  exit 2
fi

repo_root=$(git rev-parse --show-toplevel)
if [[ $(git -C "$repo_root" rev-parse HEAD) != "$sha" ]]; then
  echo "The checked-out commit does not match $sha." >&2
  exit 2
fi

short_sha=${sha:0:12}
server_image="openlab-server:$short_sha"
web_image="openlab-web:$short_sha"

image_id() {
  docker image inspect --format '{{.Id}}' "$1"
}

retained_ids=()
for image in \
  openlab-server:deployed openlab-server:rollback \
  openlab-web:deployed openlab-web:rollback; do
  if id=$(image_id "$image" 2>/dev/null); then
    retained_ids+=("$id")
  fi
done

is_retained_id() {
  local candidate=$1 retained
  for retained in "${retained_ids[@]}"; do
    [[ "$candidate" == "$retained" ]] && return 0
  done
  return 1
}

echo "Removing only superseded OpenLab image tags before the build."
while IFS= read -r ref; do
  [[ -n "$ref" ]] || continue
  id=$(image_id "$ref" 2>/dev/null || true)
  if [[ -n "$id" ]] && ! is_retained_id "$id"; then
    docker image rm "$ref" >/dev/null || true
  fi
done < <(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(openlab-(server|worker|web)|deploy-openlab-(server|worker|web)):' | sort -u)

free_kb=$(df -Pk / | awk 'NR == 2 {print $4}')
if (( free_kb < min_free_kb )); then
  echo "Free space is below the build gate; pruning only default-builder cache older than 24 hours."
  docker buildx prune --builder default --force --filter 'until=24h'
  free_kb=$(df -Pk / | awk 'NR == 2 {print $4}')
fi
if (( free_kb < min_free_kb )); then
  echo "Only ${free_kb} KiB is free; at least ${min_free_kb} KiB is required." >&2
  echo "No unrelated Docker images, builders, containers, or volumes were removed." >&2
  exit 1
fi

echo "Building native images for $sha with ${free_kb} KiB free."
docker build --pull -t "$server_image" -f "$repo_root/deploy/Dockerfile.backend" "$repo_root"
docker build --pull -t "$web_image" -f "$repo_root/deploy/Dockerfile.web" "$repo_root"

server_id=$(image_id "$server_image")
web_id=$(image_id "$web_image")
printf 'AWS server image: %s\nAWS web image: %s\n' "$server_id" "$web_id"

echo "Copying deployment configuration without touching the Pi environment file."
# The deployment root is fixed by the trusted workflow and intentionally expands locally.
# shellcheck disable=SC2029
ssh "$pi_host" \
  "mkdir -p '$pi_root/deploy/automation'; if [ -f '$pi_root/deploy/compose.yml' ]; then cp -p '$pi_root/deploy/compose.yml' '$pi_root/deploy/compose.yml.rollback'; fi"
# shellcheck disable=SC2029
tar -C "$repo_root" -cf - \
  deploy/compose.yml \
  deploy/bootstrap-secrets.sh \
  deploy/automation/pi-activate.sh \
  | ssh "$pi_host" "tar -xf - -C '$pi_root'"

load_log=$(mktemp)
trap 'rm -f "$load_log"' EXIT
echo "Streaming images to $pi_host."
docker save "$server_image" "$web_image" \
  | gzip -1 \
  | ssh "$pi_host" 'gzip -d | docker load' \
  | tee "$load_log"

if ! grep -Eq 'Loaded image:|Loaded image ID:' "$load_log"; then
  echo "docker load did not report an explicit loaded image." >&2
  exit 1
fi

# shellcheck disable=SC2029
pi_ids=$(ssh "$pi_host" \
  "docker image inspect --format '{{.Id}}' '$server_image'; docker image inspect --format '{{.Id}}' '$web_image'")
pi_server_id=$(sed -n '1p' <<<"$pi_ids")
pi_web_id=$(sed -n '2p' <<<"$pi_ids")
if [[ "$pi_server_id" != "$server_id" || "$pi_web_id" != "$web_id" ]]; then
  echo "AWS and Pi image IDs do not match; activation is blocked." >&2
  exit 1
fi

# shellcheck disable=SC2029
ssh "$pi_host" \
  "bash '$pi_root/deploy/automation/pi-activate.sh' '$short_sha' '$pi_root' '$server_id' '$web_id'"

if image_id openlab-server:deployed >/dev/null 2>&1; then
  docker tag openlab-server:deployed openlab-server:rollback
  docker tag openlab-server:deployed openlab-worker:rollback
fi
if image_id openlab-web:deployed >/dev/null 2>&1; then
  docker tag openlab-web:deployed openlab-web:rollback
fi
docker tag "$server_image" openlab-server:deployed
docker tag "$server_image" openlab-worker:deployed
docker tag "$web_image" openlab-web:deployed

deployed_server_id=$(image_id openlab-server:deployed)
rollback_server_id=$(image_id openlab-server:rollback 2>/dev/null || true)
deployed_web_id=$(image_id openlab-web:deployed)
rollback_web_id=$(image_id openlab-web:rollback 2>/dev/null || true)

while IFS= read -r ref; do
  [[ -n "$ref" ]] || continue
  id=$(image_id "$ref" 2>/dev/null || true)
  case "$id" in
    "$deployed_server_id"|"$rollback_server_id"|"$deployed_web_id"|"$rollback_web_id") ;;
    *) docker image rm "$ref" >/dev/null || true ;;
  esac
done < <(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(openlab-(server|worker|web)|deploy-openlab-(server|worker|web)):' | sort -u)

df -h /
docker system df
echo "Deployment of $sha completed and one AWS rollback release was retained."
