# OpenLab

OpenLab is a local-first inventory and build workspace for an electronics lab.

## Run it with Docker

1. Optionally copy the environment template if you want to provide deployment-specific
   values. The bootstrap command below creates it automatically when it is missing.

   ```powershell
   Copy-Item .env.example .env
   ```

2. Bootstrap persistent secrets and start the stack.

   ```bash
   sh deploy/up.sh --build
   ```

   The bootstrap step generates `OPENLAB_SECRET_KEY`,
   `OPENLAB_ENCRYPTION_KEY`, and the initial PostgreSQL password when they are
   missing. Existing non-empty secrets are preserved, so restarting or
   redeploying the stack does not invalidate sessions or stored provider keys.
   Do not delete `.env` or regenerate `OPENLAB_ENCRYPTION_KEY` unless you are
   intentionally rotating secrets and will re-enter provider API keys.

3. Open `http://localhost:3000`, copy the one-time setup token printed by
   `openlab-server`, then create the owner account.

The Compose stack includes PostgreSQL/pgvector, FastAPI, the PostgreSQL worker,
and the Next.js web app. Stop it with `docker compose --env-file .env -f deploy/compose.yml down`.
Your database and attachment volumes are retained.

## Configure Smart Inbox

Open **Inbox → Smart Inbox model** after signing in. Set a compatible endpoint
and model, then enable it. The same adapter supports OpenRouter, OpenAI, Ollama,
LM Studio, vLLM, and other services exposing `/v1/chat/completions`.

- Local Ollama on the Docker host: `http://host.docker.internal:11434/v1`
- OpenRouter: `https://openrouter.ai/api/v1`
- OpenAI: `https://api.openai.com/v1`

For an endpoint needing an API key, `deploy/up.sh` ensures that
`OPENLAB_ENCRYPTION_KEY` exists before the provider configuration is saved.
The key is stored encrypted and never returned through the API. The Inbox
displays whether captured data stays local or leaves the server before it is
processed.

## Configure the lab and KiCad

Open **Settings** as the lab owner to change the lab name, measurement system,
AI provider, and optional KiCad command. The KiCad value is a command or path
inside the worker container, not a path on the Docker host. A saved Settings
value takes precedence over `OPENLAB_KICAD_CLI`; clearing it restores the
environment fallback.

The standard Raspberry Pi image does not install KiCad. Use a custom
KiCad-enabled backend/worker image, then enter `kicad-cli` or its absolute
container path and run **Check again**. The Deployment section lists the other
supported environment variables without returning secrets. Edit those values
in the repository-root `.env` and recreate the affected services.

See [MVP 1 Smart Inbox](docs/MVP1_SMART_INBOX.md) and
[remaining scope](docs/REMAINING_FEATURES.md) for implementation status.

## Print drawer labels

Open **Locations**, choose a drawer, and preview, download, or print its QR label. Scanning the
label opens Capture with that drawer preselected. Set `OPENLAB_PUBLIC_URL` to a stable address such
as `http://pi3b.local:3000` before printing permanent labels; otherwise OpenLab uses the browser
address shown on the label screen.

## Repository layout

- `backend/` — FastAPI modular monolith, PostgreSQL worker, and migrations.
- `web/` — Next.js PWA and generated OpenAPI types.
- `deploy/` — Docker Compose, Dockerfiles, backup, and restore scripts.
