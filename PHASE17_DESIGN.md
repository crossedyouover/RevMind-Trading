# Phase 17 — Point-in-Time Insider Transaction Facts

Status: design draft, not an implementation freeze.

Base: `01e17a57f57a822954b76feedd7da4e623d92ea8`, containing frozen Phase 16
`eb73f506c1f78b1b4bd4c225ce8c93abd3f318a2` and its published handoff update.

## Scope

Add provider-neutral source-fact contracts and pure PIT selection for individual insider
transactions. The broader roadmap's flow-evidence objective is not satisfied by insider records;
order flow, fund flows, ownership aggregates, and inferred institutional activity remain separate
future designs. Do not call insider transactions a proxy for those datasets.

Do not modify frozen Phase 1–16 contracts, including the existing placeholder `InsiderActivity`.
Use a new, narrow package for the observation-aware contract.

## Proposed observation

`ObservedInsiderTransaction` contains:

- Caller-supplied UUID4 observation identity; no clock or UUID generation in pure models/engines.
- Required `observed_at`: timezone-aware, normalized UTC knowledge boundary.
- Required canonical source identity and complete canonical instrument identity.
- Required source-supplied reporting-person name and transaction code; optional source role.
- Optional source transaction date, represented as a calendar date, not fabricated midnight UTC.
- Optional source filing timestamp, timezone-aware when present; no inference from receipt time.
- Optional finite nonnegative Decimal quantity, unit price, and reported total value.
- Optional source transaction ID, filing ID, revision ID, and source URL.

Missing values stay absent. Zero is a reported zero, not a missing-value sentinel. Do not derive
reported total value from quantity and price, infer direction from quantity sign, or interpret
transaction codes in this phase. Record source assertions without asserting their economic meaning.

## Knowledge boundary and revisions

Input must be an immutable tuple of canonical observations in strict
`(observed_at, observation_id)` order, with unique observation IDs. Reject every input observation
after the explicit `as_of`; publication or transaction time never makes it eligible earlier.

Requests require exactly one source. Filter by that source before reducing revisions. A source
transaction ID must identify an individual transaction across revisions, not an entire filing or
a row number invented by RevMind. Source adapters must explicitly establish that meaning before
supplying it. Without such an ID, receipts remain independent facts, including identical receipts.

Within the selected source, the last knowledge-ordered receipt for each supplied transaction ID
is the selected version. This is a deterministic latest-received view, not proof of a publisher's
revision chronology. Optional revision IDs are retained as provenance, not interpreted or sorted.
This phase does not implement deletion/tombstone semantics.

Reduce revisions before applying instrument, transaction-date, or filing-time filters. Otherwise
an old version could be incorrectly resurrected when a newer correction changes a filter field.
Retain all original facts upstream; selection must not mutate or delete observations.

## Request and result

The immutable request fixes `as_of`, source, and optional exact instrument, transaction-date range,
and filing-time range. Both ranges are half-open with independently optional bounds. Unknown dates
or times fail only the corresponding requested range; they are never guessed. Dates and timestamps
are different types and are never compared interchangeably.

The immutable result echoes the request and retains full selected observation envelopes in strict
knowledge order. Counts distinguish inspected receipts, selected-source receipts, revision winners,
and final matching winners. Counts must be strict nonnegative integers with consistent relations.
Empty input and empty selection are successful, explicit results.

Direct construction must validate request filters, source, cutoff, ordering, duplicate identities,
and uniqueness of keyed transaction winners. Engine entry must defensively reconstruct nested
contracts so `model_copy`/`model_construct` cannot bypass validation. Structural validation cannot
prove external source authenticity or that a caller supplied complete history; document that limit.

## Boundary and failure behavior

No storage, provider, HTTP, credentials, file access, clock, random identity, scheduling, retry,
source fallback, sorting of invalid input, inference, sentiment, scoring, ranking, recommendation,
LLM, risk decision, alert, broker execution, or Angelo OS integration.

Malformed requests and inputs fail before selection. Typed computation failures retain their cause.
Unexpected programming failures are not silently converted into an empty successful result.
Existing deterministic risk veto authority and future control-plane separation remain unchanged.

## Acceptance tests before implementation freeze

1. Exact cutoff inclusion and one-microsecond future rejection, regardless of filing/event time.
2. Equal receipt times resolved by UUID order; reversed input and duplicate IDs rejected.
3. Revisions selected before filters, including corrections moving an instrument or date out of range.
4. Same transaction ID across sources never blends; unkeyed repeated receipts remain distinct.
5. Date and UTC timestamp validation, offset normalization, unknown-time filtering, half-open bounds.
6. Decimal precision, zero preservation, optional absence, negative/nonfinite rejection, no inference.
7. Nested forged-model rejection, immutable tuples, unknown-field rejection, deterministic JSON round trip.
8. Direct-result filter/order/provenance checks, consistent counts, empty and repeated execution.
9. Forbidden-dependency tests plus full regression suite, Ruff, strict mypy, and whitespace checks.

Do not tag or describe Phase 17 as frozen until implementation, tests, review, merge validation,
and remote reference verification have actually completed.
