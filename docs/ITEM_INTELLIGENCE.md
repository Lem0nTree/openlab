# Item intelligence and BUILD workflow

OpenLab treats capture evidence, model confidence, and human decisions as separate data.

## Inbox

- Text, image, audio, email, and PDF captures enter the same asynchronous Inbox flow.
- Raw attachments are deleted after processing; normalized text and candidate provenance remain.
- The provider returns strict candidate JSON. Unsupported strict-schema endpoints fall back to JSON
  object mode, followed by Pydantic validation, one repair attempt, and an unresolved fallback.
- `identity_confidence` is one of `high`, `medium`, `low`, or `unresolved`. It is never changed by
  confirmation.
- Candidate workflow status is `proposed`, `confirmed`, `ignored`, or `received`.
- Product pages are retained as provenance and reclassified into a concise identity. The fetched
  proposal always returns to human review; order quantity is not replaced by listing content.

## Canonical profiles and retrieval

Only confirmed Thing data is embedded: name, optional approved short description, aliases,
category, capabilities, interfaces, and accepted or verified facts. Marketplace titles, email
content, quantity, and location are excluded.

Embeddings use the configured embedding model at the same OpenAI-compatible endpoint as capture
processing. Storage uses PostgreSQL `vector`. Retrieval combines exact identity, text overlap, and
semantic similarity, then adds current available quantity and locations to the result.

## BUILD proposals

BUILD planning is asynchronous and bounded to five matches per role, 500 combinations, and three
returned solutions. A proposal can combine owned items and separately lists unsatisfied generic
constraints as `Component required`. Temporary proposals expire after 24 hours.

Accepting a solution writes planner-owned requirements and the chosen design to the project. It
does not reserve, consume, or move inventory. Physical allocation remains a separate explicit
action. Manual requirements are never overwritten by the planner.

## Pinouts and schematics

Pin records carry their source reference and verification state. Schematic generation uses only
saved pin IDs and runs deterministic checks for unknown pins, duplicate pin use, output conflicts,
power and ground coverage, recorded voltage compatibility, I2C address collisions, and missing
pull-up confirmation.

The accepted wiring plan can be downloaded as a `.kicad_sch` containing generic sourced-pin
symbols, their recorded electrical types, and actual nets. If
`OPENLAB_KICAD_CLI` points to `kicad-cli` inside the worker environment, the proposal job also runs
`kicad-cli sch erc`. The standard Raspberry Pi image intentionally does not include KiCad.
