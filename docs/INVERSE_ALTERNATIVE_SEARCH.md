# Inverse alternative search

The **Alternatives** workspace answers a different question from BUILD planning:
given the name of a component or board, what currently available lab stock can
replace its required function? Each search represents one target unit. Users
may add an intended use to constrain the interpretation.

## Knowledge and privacy

OpenLab resolves the target in this order:

1. Exact reviewed local Thing, alias, MPN, or curated common-module record.
2. The configured OpenAI-compatible provider, if enabled, using only local
   context and a strict response schema.
3. `insufficient_target_knowledge` when neither source establishes its role.

The feature never performs live web research. Every result records whether the
analysis used reviewed local data or model inference, and whether any provider
processing was local or external.

## Stock and evidence boundaries

Only unarchived inventory with positive quantity after reservations is eligible.
OpenLab checks direct stock first, then ranks one-piece and bounded multi-piece
solutions. A search considers at most four non-overlapping functional roles,
five candidates per role, 500 combinations, four physical line items, and
three recommendations.

Recommendations are one of:

- `documented_match` — accepted or verified local records support the required
  behavior.
- `needs_validation` — functional coverage is plausible, but electrical, pin,
  voltage, form-factor, or connection evidence is incomplete.
- `insufficient_evidence` — not actionable and unavailable for Build creation.

A functional alternative is never called drop-in compatible unless all required
electrical, pin, and form-factor evidence is recorded and verified.

## Create a Build

Searches are resumable for 24 hours. For documented matches and validation-needed
options, **Create Build** rechecks job expiry and stock, then creates (or returns)
an idempotent BUILD project. The project receives one requirement per physical
line item, with covered functional roles in its constraints and source analysis
in `design_json`.

Creating a Build does not reserve, consume, or move inventory. Allocation,
wiring review, and validation stay explicit user decisions in BUILD.
