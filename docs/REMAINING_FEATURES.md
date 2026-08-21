# Remaining implementation scope

This document records what remains after the current foundation, MVP 0 core
inventory, selected MVP 2 project flows, and MVP 1 Smart Inbox work.

## MVP 0 — Core inventory

- Thing image management and Thing detail/edit screens.
- Stock adjustment, history, and dedicated mobile receive/move workflows.
- QR scanning and print-label workflow; current labels are generated SVGs.
- Full PostgreSQL fuzzy/full-text search and location-tree/detail screens.
- Display-unit preferences and complete backup/restore validation/migration docs.

## MVP 1 — Inbox follow-through

- Live-provider contract tests using a deterministic mock server.
- Provider-specific capability detection and clearer voice-model compatibility.
- UI support for matching a candidate to an existing Thing before confirmation.
- Cancellation controls, retry controls, source-artifact preview, and richer
  field-level evidence presentation.

## MVP 2 — Projects and BUILD

- BOM import, coverage and missing-parts results, candidate substitutions, and
  a project history view.
- Recovery/dismantle UI and project-aware consumption workflow.
- Broader concurrent inventory and allocation integration tests.

## MVP 3 — Structured hardware knowledge

- APIs and editors for capabilities, interfaces, pinouts, relationships, and
  documents.
- Datasheet attachment/enrichment review and trust/provenance inspection.
- Full deterministic compatibility explanations using typed electrical facts.

## MVP 4 — Semantic retrieval and intelligence

- Native pgvector embedding storage, active embedding spaces, and re-embedding jobs.
- OpenRouter/Ollama-compatible embeddings configuration and semantic retrieval.
- Typed inventory/knowledge/project orchestrator services.
- AI-assisted build decomposition, build checks, and “What can I build?” UI
  grounded in actual stock and compatibility evidence.

## Release and verification

- PostgreSQL property/concurrency tests, Playwright and accessibility coverage.
- Backup/restore acceptance validation and contract compatibility checks.
- ARM64 image build, physical Pi Zero 2 W performance testing, and release gates.
