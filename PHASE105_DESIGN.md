# Phase 105 — Append-Only Global-Event Store

Status: **frozen design for durable point-in-time event history**.

## Objective

Persist canonical `ObservedGlobalEvent` receipts without mutation and materialize only receipts
whose RevMind knowledge time is at or before an explicit request cutoff.

## Deterministic rules

1. The SQLite database is file-backed, STRICT, schema-versioned, and owns one append-only table.
2. Observation identity is the primary key; repeated identities fail and never overwrite history.
3. Batch appends are atomic and validate every canonical event before writing.
4. Canonical JSON is authoritative. Query projections are checked against it on every read.
5. Point-in-time reads use only `observed_at <= as_of`, ordered by
   `(observed_at, observation_id)`.
6. Materialization delegates to the frozen Phase 103 deterministic engine after the bounded read.
7. Corrupt schemas, malformed JSON, mismatched projections, and unsupported versions fail closed.

## Explicit non-goals

- event deletion, update, deduplication by provider identity, or source reconciliation;
- retention, network services, polling, UI, World Monitor, or autonomous research;
- exposure/transmission inference, trading signals, portfolio actions, alerts, or execution.
