# Phase 102 — Canonical Global-Event Observation Contracts

Status: **frozen design for the first implementation slice of the approved global-event and
market-transmission roadmap**.

## Objective

Define immutable, provider-neutral contracts for recording what an external source asserted about
a global event and when RevMind learned it. This phase creates facts only. It does not determine
market impact, exposure, transmission, direction, relevance, confidence, or action.

## Contract

`ObservedGlobalEvent` preserves one complete provider receipt:

- `observation_id` is a caller-supplied UUID4 receipt identity;
- `observed_at` is the only knowledge-time boundary;
- `occurred_at` and `published_at` are optional source assertions and never grant early
  eligibility;
- `source_event_id` identifies a provider record when supplied;
- `source_revision_id` identifies a provider revision only when `source_event_id` is also known;
- category, headline, source description, URL, countries, regions, and instruments remain exactly
  attributed source facts;
- ordered collections must already be unique and canonical. The model does not silently sort,
  normalize, deduplicate, geocode, map, or enrich them.

`GlobalEventReceiptBatch` is an immutable single-source, single-receipt envelope. Every event in a
batch must share its source and `observed_at`, and events must be ordered by
`(observed_at, observation_id)`.

## Safety invariants

1. Event and publication time never substitute for `observed_at`.
2. Unknown timestamps, locations, instruments, and revisions remain unknown.
3. Repeated receipts and corrections remain distinct observations.
4. Provider wire formats never cross this boundary.
5. Categories describe source facts; they are not signals or predictions.
6. No LLM, network, storage, market-transmission, ranking, portfolio, risk, alert, or execution
   behavior is introduced.

## Explicit non-goals

- World Monitor or any other live provider adapter;
- event storage or point-in-time materialization;
- source reconciliation or reliability scoring;
- exposure or transmission graphs;
- cross-asset evidence;
- UI/API integration;
- trading signals, recommendations, alerts, orders, or real-money authority.
