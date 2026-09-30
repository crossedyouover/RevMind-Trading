# Phase 103 — Point-in-Time Global-Event Materialization

Status: **frozen design for deterministic source/time/revision semantics**.

## Objective

Materialize one provider's global-event receipts at an explicit knowledge cutoff. The engine
selects facts; it does not infer market impact, exposure, transmission, direction, relevance,
confidence, or action.

## Deterministic rules

1. Inputs must be canonical `ObservedGlobalEvent` values in strict
   `(observed_at, observation_id)` order.
2. Every supplied receipt must satisfy `observed_at <= as_of`; future-known input fails closed.
3. The request names exactly one source. Other sources are inspected but never selected.
4. Filters are applied before revision selection and use half-open time ranges.
5. When `source_event_id` is present, the last eligible receipt in knowledge order wins.
6. Receipts without `source_event_id` remain distinct; no identity is invented.
7. `source_revision_id` is provenance only. It never overrides receipt order.
8. Output order is deterministic by source occurrence time when known, then publication time when
   known, then knowledge order.

## Explicit non-goals

- persistence or provider adapters;
- source reconciliation or trust scoring;
- World Monitor integration;
- exposure/transmission graphs or cross-asset evidence;
- LLM analysis, UI/API changes, alerts, portfolio actions, or execution.
