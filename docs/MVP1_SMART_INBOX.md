# MVP 1 — Smart Inbox

## Purpose

Smart Inbox turns a capture into reviewable inventory candidates. It never writes
stock based on model output alone: a person must correct the candidate, choose a
physical location, and confirm receipt.

## Implemented flow

1. Capture text, a photo, screenshot, voice file, or PDF artifact in the PWA.
2. Store each attachment in the local content-addressed volume with SHA-256,
   MIME type, original name, and database metadata.
3. Show whether processing stays local or sends source material to an external
   endpoint.
4. Create a PostgreSQL job and transition the Inbox item through `captured`,
   `queued`, `processing`, and `needs_review`. Provider failures set `failed`
   and leave the job retryable; no inventory write occurs.
5. Validate extracted candidates against the versioned API schema and retain
   confidence plus source/provider/model provenance.
6. Review, edit, select a destination, and confirm. Confirmation creates or
   reuses a Thing and records one idempotent inventory receipt.

## Single provider integration

OpenLab uses an OpenAI-compatible adapter, not a vendor SDK. Configure one
endpoint, model ID, optional API key, and enabled state in **Inbox → Smart Inbox
model**. This works with OpenRouter, OpenAI, Ollama, LM Studio, vLLM, and many
other compatible services.

- Text and image captures use `/v1/chat/completions` with JSON-mode fallback.
- Voice uses `/v1/audio/transcriptions` when the selected endpoint implements it.
- PDFs are preserved as artifacts; document-specific extraction remains outside
  MVP 1 as planned.
- The model-list control calls `/v1/models` when provided by the endpoint.

API keys are encrypted with `OPENLAB_ENCRYPTION_KEY` using Fernet. They are not
returned through API responses, embedded in the browser, or written to normal
application logs. A local endpoint is labelled local; other endpoints are
labelled external before the capture is processed.

## Offline behavior

AI remains disabled by default. Text captures still receive a conservative
local candidate: `7 x MCP23017` becomes quantity `7` and a generic candidate
name. Photo, voice, and other modality processing needs an enabled compatible
endpoint, but their original artifacts remain stored locally.

## Relevant API surface

- `GET` / `PUT /api/v1/ai/provider` — owner-only safe configuration; secrets
  are never returned.
- `GET /api/v1/ai/provider/models` — optional compatible-endpoint model list.
- `POST /api/v1/inbox` — create the capture record.
- `POST /api/v1/inbox/{id}/attachments` — store a source artifact.
- `POST /api/v1/inbox/{id}/process` — queue analysis.
- `GET /api/v1/inbox/{id}/candidates` — retrieve review candidates.
- `POST /api/v1/inbox/{id}/confirm` — idempotently commit human-confirmed stock.
