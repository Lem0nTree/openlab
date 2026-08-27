# Remaining implementation scope

This document records what remains after the current foundation, the review-first
multimodal Inbox, canonical item intelligence, semantic inventory retrieval, and
the first bounded BUILD and schematic proposal flows.

## MVP 0 — Core inventory

- Thing image management.
- Dedicated camera-scanner and high-volume mobile receive/move workflows.
- Full PostgreSQL fuzzy/full-text search.
- Display-unit preferences and complete backup/restore validation/migration docs.

## MVP 1 — Inbox follow-through

- Live-provider contract tests using a deterministic mock server.
- Provider-specific capability detection and clearer voice-model compatibility.
- UI support for matching a candidate to an existing Thing before confirmation.
- Cancellation controls, retry controls, source-artifact preview, and richer
  field-level evidence presentation.
- More marketplace-specific product-page extractors beyond the current safe,
  generic title/description enrichment.

## MVP 2 — Projects and BUILD

- BOM import, coverage and missing-parts results, candidate substitutions, and
  a project history view.
- Recovery/dismantle UI and project-aware consumption workflow.
- Broader concurrent inventory and allocation integration tests.
- A general “What can I build?” explorer; the implemented flow starts from a
  specific project goal and returns bounded owned-item combinations plus a
  `Component required` list.
- Richer inverse-search compatibility evidence beyond the implemented Alternatives
  workspace, including broader reviewed electrical, pin, and physical-fit records.

## MVP 3 — Structured hardware knowledge

- Full editors for capabilities, interfaces, pinouts, relationships, and
  documents; the capability/interface and pinout APIs are available, but the
  dedicated knowledge-management UI is still limited.
- Datasheet attachment/enrichment review and richer trust/provenance inspection.
- Full deterministic compatibility explanations using typed electrical facts.
- Standard KiCad library-symbol mapping, board-level footprints, and optional
  SPICE simulation. The current exporter produces a reviewable generic-pin
  schematic and can run KiCad ERC when the CLI is configured.

## MVP 4 — Semantic retrieval and intelligence

- Embedding-space migration/version management beyond the current configured
  profile space.
- Richer typed compatibility rules and explanation coverage.
- Background model/provider observability, evaluation datasets, and retrieval
  quality benchmarks.

## Release and verification

- PostgreSQL property/concurrency tests, Playwright and accessibility coverage.
- Backup/restore acceptance validation and contract compatibility checks.
- ARM64 image build, physical Pi Zero 2 W performance testing, and release gates.
