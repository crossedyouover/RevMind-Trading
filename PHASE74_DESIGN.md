# Phase 74 — Current Account Snapshot

## Purpose

Make the result of a combined Myfxbook refresh immediately understandable without requiring the
operator to read every detailed fact row.

## In scope

- Add a compact snapshot region to the current-exposure step.
- After a fully successful explicit combined refresh, show provider-reported balance and equity,
  plus exact counts of open positions and pending orders.
- Show the provider observation time and clearly label the snapshot as factual read-only context.
- Keep the detailed summary, positions, orders, and bounded recent transactions below the snapshot.
- Clear or mark the snapshot incomplete when either combined request fails.

## Non-goals and safety

- No P/L interpretation, health score, exposure rating, prediction, recommendation, signal, or
  trade action.
- No aggregation across currencies, provider accounts, observations, or refresh cycles.
- No automatic request, polling, retry, fallback, new endpoint, or new provider session.
- No research, risk, execution, storage, or domain-contract change.

## Verification

- Source tests assert the snapshot fields, success-only rendering, partial-failure clearing, factual
  disclaimer, and absence of automatic requests, timers, or order endpoints.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
