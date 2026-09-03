# Phase 16 — Point-in-Time Catalyst and News Evidence

## Objective

Add provider-neutral, immutable source-fact contracts and deterministic PIT materialization for
news and catalysts. Phase 16 records what RevMind knew and when; it does not interpret sentiment,
predict price movement, rank opportunities, or authorize risk or execution.

## Time model

- `published_at`: source event time; optional when the source does not provide a trustworthy time.
- `observed_at`: required RevMind knowledge boundary.
- Eligibility is only `observed_at <= as_of`.
- Canonical order is `(observed_at, observation_id)`.
- Publication time never grants early visibility.

## Canonical fact

`ObservedCatalystFact` contains UUID4 identity, immutable headline, optional body/summary supplied
by the source, canonical source identity, authority class (`PRIMARY` or `SECONDARY`), optional URL,
optional source record ID, zero or more complete canonical instrument identities, publication
time, and observation time. Provider wire fields never cross this boundary.

## Revision policy

Every receipt remains a distinct fact. Materialization requires one explicit source and never
blends providers. When a source record ID exists, the latest knowledge-ordered version known at
`as_of` is selected for that record. Facts without a source record ID remain distinct and are not
content-hash deduplicated. Output retains selected-observation provenance.

## Deterministic materialization request

The request fixes `as_of`, one explicit source, optional instrument filter, optional authority
filter, and optional half-open publication-time range. Unknown publication time is retained unless
a publication-time range was requested, in which case it is ineligible rather than guessed.

## Fail-closed rules

Reject noncanonical facts, duplicate observation IDs, non-increasing knowledge order, future-known
input, contradictory source identity, invalid ranges, and forged provenance. Never sort or repair
invalid replay input; canonically order selected output by publication time (unknown last), then
knowledge order.

## Non-goals

No external news provider, fetching, HTML parsing, LLM summarization, sentiment, entity inference,
instrument inference, scoring, ranking, recommendation, storage schema, scheduler, scanner change,
portfolio action, risk decision, alert, broker operation, or Angelo OS coupling.

## Completion gate

Add immutable models, a pure engine, adversarial PIT/revision/source tests, README documentation,
and forbidden-dependency audits. Then pass focused tests, the full suite, Ruff, strict mypy,
`git diff --check`, scope review, merge review, annotated freeze tag, push, and peeled remote-tag
verification.
