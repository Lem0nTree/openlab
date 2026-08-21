# OpenLab

OpenLab is a local-first inventory and build workspace for an electronics lab.

## Run it with Docker

1. Copy the environment template and replace the two password/secret placeholders.

   ```powershell
   Copy-Item .env.example .env
   ```

2. Start the stack.

   ```powershell
   docker compose -f deploy/compose.yml up --build
   ```

3. Open `http://localhost:3000`, copy the one-time setup token printed by
   `openlab-server`, then create the owner account.

The Compose stack includes PostgreSQL/pgvector, FastAPI, the PostgreSQL worker,
and the Next.js web app. Stop it with `docker compose -f deploy/compose.yml down`.
Your database and attachment volumes are retained.

## Configure Smart Inbox

Open **Inbox → Smart Inbox model** after signing in. Set a compatible endpoint
and model, then enable it. The same adapter supports OpenRouter, OpenAI, Ollama,
LM Studio, vLLM, and other services exposing `/v1/chat/completions`.

- Local Ollama on the Docker host: `http://host.docker.internal:11434/v1`
- OpenRouter: `https://openrouter.ai/api/v1`
- OpenAI: `https://api.openai.com/v1`

For an endpoint needing an API key, generate `OPENLAB_ENCRYPTION_KEY` before
saving the provider configuration. The key is stored encrypted and never
returned through the API. The Inbox displays whether captured data stays local
or leaves the server before it is processed.

See [MVP 1 Smart Inbox](docs/MVP1_SMART_INBOX.md) and
[remaining scope](docs/REMAINING_FEATURES.md) for implementation status.

## Repository layout

- `backend/` — FastAPI modular monolith, PostgreSQL worker, and migrations.
- `web/` — Next.js PWA and generated OpenAPI types.
- `deploy/` — Docker Compose, Dockerfiles, backup, and restore scripts.
