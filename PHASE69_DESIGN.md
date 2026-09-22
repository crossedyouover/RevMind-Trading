# Phase 69 — Explicit Myfxbook Daily Performance Endpoint

## Purpose

Expose the frozen provider-reported daily performance boundary through one authenticated local
request with operator-supplied inclusive dates and no hidden current-time assumption.

## In scope

- One strict request contract containing schema version, `start`, and `end` ISO calendar dates.
- Inclusive range validation with `start <= end` and at most 366 dates.
- Load the exact saved account ID, validated timezone, and local credential pair.
- Invoke the frozen `daily_performance` boundary once and return canonical observations in order.
- Echo the exact requested range, count, disconnected session state, and execution `NONE`.
- Always terminally disconnect before returning success or failure.
- Scripted tests only; no quality-gate request reaches Myfxbook.

## Non-goals and safety

- No default range, hidden clock, automatic request, polling, persistence, aggregation, or charting.
- No extrapolation, benchmark, prediction, ranking, recommendation, signal, or trade plan.
- No use of performance facts in deterministic risk or execution decisions.
- No credentials/session token in responses, errors, logs, or storage.
- No fallback account and no execution or broker mutation capability.

## Verification

- Tests prove strict envelope/date/range validation, exact account binding, canonical ordering,
  mandatory disconnect, redaction, local authentication, and fail-closed configuration.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
