#!/usr/bin/env sh
set -eu

# Create or complete the deployment environment without rotating existing
# secrets. Run this from the repository or through deploy/up.sh before Compose.

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file=${OPENLAB_ENV_FILE:-"$repo_root/.env"}
example_file="$repo_root/.env.example"

die() {
    printf 'bootstrap-secrets: %s\n' "$1" >&2
    exit 1
}

if [ ! -f "$env_file" ]; then
    [ -f "$example_file" ] || die "missing $example_file"
    umask 077
    cp "$example_file" "$env_file"
    printf 'Created %s from .env.example\n' "$env_file"
fi

umask 077

env_value() {
    key=$1
    awk -v key="$key" '
        $0 ~ ("^[[:space:]]*" key "[[:space:]]*=") {
            line = $0
            sub("^[[:space:]]*" key "[[:space:]]*=", "", line)
            print line
            found = 1
            exit
        }
        END {
            if (!found) exit 1
        }
    ' "$env_file"
}

set_env_value() {
    key=$1
    value=$2
    temp_file="$env_file.tmp.$$"

    awk -v key="$key" -v value="$value" '
        $0 ~ ("^[[:space:]]*" key "[[:space:]]*=") {
            if (!replaced) {
                print key "=" value
                replaced = 1
            }
            next
        }
        { print }
        END {
            if (!replaced) print key "=" value
        }
    ' "$env_file" > "$temp_file"
    mv "$temp_file" "$env_file"
}

generate_secret() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
    elif command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 48
    else
        die 'python3 or openssl is required to generate secrets'
    fi
}

generate_fernet_key() {
    if command -v python3 >/dev/null 2>&1 && \
        python3 -c 'import cryptography' >/dev/null 2>&1; then
        python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
    elif command -v openssl >/dev/null 2>&1; then
        # A Fernet key is URL-safe base64 encoding of exactly 32 random bytes.
        openssl rand -base64 32 | tr '+/' '-_' | tr -d '\r\n'
    else
        die 'python3 with cryptography or openssl is required to generate OPENLAB_ENCRYPTION_KEY'
    fi
}

secret_key=$(env_value OPENLAB_SECRET_KEY 2>/dev/null || true)
if [ -z "$secret_key" ] || [ "$secret_key" = 'replace-with-a-long-random-session-secret' ]; then
    set_env_value OPENLAB_SECRET_KEY "$(generate_secret)"
    printf 'Generated OPENLAB_SECRET_KEY\n'
else
    printf 'Preserved OPENLAB_SECRET_KEY\n'
fi

encryption_key=$(env_value OPENLAB_ENCRYPTION_KEY 2>/dev/null || true)
if [ -z "$encryption_key" ]; then
    set_env_value OPENLAB_ENCRYPTION_KEY "$(generate_fernet_key)"
    printf 'Generated OPENLAB_ENCRYPTION_KEY\n'
else
    printf 'Preserved OPENLAB_ENCRYPTION_KEY\n'
fi

# Generate the PostgreSQL password only when creating a brand-new environment.
# Changing it after PostgreSQL has initialized would break the DATABASE_URL.
postgres_password=$(env_value POSTGRES_PASSWORD 2>/dev/null || true)
if [ "$postgres_password" = 'replace-with-a-unique-postgres-password' ] || [ -z "$postgres_password" ]; then
    # If .env was lost or copied from the example but the database volume
    # survived, generating a new password would make PostgreSQL unreachable.
    # Fail closed instead.
    # Compose derives its default project from the first compose file's directory,
    # not the repository root. The old check missed deploy_openlab-postgres.
    compose_project=${COMPOSE_PROJECT_NAME:-$(env_value COMPOSE_PROJECT_NAME 2>/dev/null || true)}
    compose_project=${compose_project:-$(basename "$script_dir")}
    postgres_volume="${compose_project}_openlab-postgres"
    if command -v docker >/dev/null 2>&1 && docker volume inspect "$postgres_volume" >/dev/null 2>&1; then
        die "the existing PostgreSQL volume $postgres_volume was found but .env has no database password; restore the original .env instead of generating a new one"
    fi
    postgres_password=$(generate_secret)
    set_env_value POSTGRES_PASSWORD "$postgres_password"
    database_url=$(env_value DATABASE_URL 2>/dev/null || true)
    if [ -z "$database_url" ]; then
        database_url="postgresql+psycopg://openlab:${postgres_password}@postgres:5432/openlab"
    else
        database_url=$(printf '%s\n' "$database_url" | sed "s/replace-with-a-unique-postgres-password/$postgres_password/g")
    fi
    set_env_value DATABASE_URL "$database_url"
    printf 'Generated POSTGRES_PASSWORD and updated DATABASE_URL\n'
fi

chmod 600 "$env_file" 2>/dev/null || true
printf 'Secrets are ready in %s\n' "$env_file"
