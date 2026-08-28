#!/usr/bin/env bash
set -Eeuo pipefail

short_sha=${1:-}
root=${2:-/home/pi/openlab}
expected_server_id=${3:-}
expected_web_id=${4:-}

if [[ ! "$short_sha" =~ ^[0-9a-f]{12}$ ]]; then
  echo "Expected a 12-character lowercase commit SHA." >&2
  exit 2
fi
if [[ "$root" != /home/pi/openlab ]]; then
  echo "Refusing an unexpected deployment root: $root" >&2
  exit 2
fi

server_image="openlab-server:$short_sha"
web_image="openlab-web:$short_sha"
compose_file="$root/deploy/compose.yml"
rollback_compose="$root/deploy/compose.yml.rollback"
env_file="$root/.env"

cd "$root"
test -s "$env_file"
test -f "$compose_file"
test "$(docker image inspect --format '{{.Id}}' "$server_image")" = "$expected_server_id"
test "$(docker image inspect --format '{{.Id}}' "$web_image")" = "$expected_web_id"

old_server_id=$(docker image inspect --format '{{.Id}}' deploy-openlab-server:latest 2>/dev/null || true)
old_web_id=$(docker image inspect --format '{{.Id}}' deploy-openlab-web:latest 2>/dev/null || true)
if [[ -n "$old_server_id" ]]; then
  docker tag "$old_server_id" openlab-server:rollback
  docker tag "$old_server_id" openlab-worker:rollback
fi
if [[ -n "$old_web_id" ]]; then
  docker tag "$old_web_id" openlab-web:rollback
fi

docker tag "$server_image" deploy-openlab-server:latest
docker tag "$server_image" deploy-openlab-worker:latest
docker tag "$web_image" deploy-openlab-web:latest

compose() {
  docker compose --env-file "$env_file" -f "$compose_file" "$@"
}

wait_for_stack() {
  local status
  status=missing
  for _ in $(seq 1 36); do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' deploy-openlab-server-1 2>/dev/null || true)
    if [[ "$status" == healthy ]]; then
      break
    fi
    sleep 10
  done
  [[ "$status" == healthy ]]
  [[ $(docker inspect --format '{{.State.Health.Status}}' deploy-postgres-1) == healthy ]]
  [[ $(docker inspect --format '{{.State.Status}}' deploy-openlab-worker-1) == running ]]
  [[ $(docker inspect --format '{{.State.Status}}' deploy-openlab-web-1) == running ]]
  [[ $(docker inspect --format '{{.Image}}' deploy-openlab-server-1) == "$expected_server_id" ]]
  [[ $(docker inspect --format '{{.Image}}' deploy-openlab-worker-1) == "$expected_server_id" ]]
  [[ $(docker inspect --format '{{.Image}}' deploy-openlab-web-1) == "$expected_web_id" ]]

  local application_ready route
  application_ready=false
  for _ in $(seq 1 60); do
    if docker logs --tail 200 deploy-openlab-worker-1 2>&1 | grep -qF 'OpenLab worker started'; then
      application_ready=true
      for route in / /login /setup /settings /api/v1/health /api/v1/setup; do
        if ! curl --fail --silent --output /dev/null "http://127.0.0.1:3000$route"; then
          application_ready=false
          break
        fi
      done
    fi
    [[ "$application_ready" == true ]] && break
    sleep 2
  done
  [[ "$application_ready" == true ]]
}

rollback() {
  local failure=$?
  trap - ERR
  echo "Activation failed; restoring the previous OpenLab image aliases." >&2
  if [[ -n "$old_server_id" && -n "$old_web_id" ]]; then
    docker tag openlab-server:rollback deploy-openlab-server:latest
    docker tag openlab-worker:rollback deploy-openlab-worker:latest
    docker tag openlab-web:rollback deploy-openlab-web:latest
    if [[ -f "$rollback_compose" ]]; then
      cp -p "$rollback_compose" "$compose_file"
    fi
    compose down --remove-orphans || true
    compose up -d --no-build --force-recreate || true
  fi
  exit "$failure"
}
trap rollback ERR

sh "$root/deploy/bootstrap-secrets.sh"
compose down --remove-orphans
compose up -d --no-build --force-recreate
wait_for_stack
trap - ERR

docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep '^deploy-openlab-'
curl --fail --silent --show-error http://127.0.0.1:3000/api/v1/health
printf '\nActivated OpenLab %s successfully.\n' "$short_sha"
