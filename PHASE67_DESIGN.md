# Phase 67 — Selected Myfxbook Account Fact Batch Endpoint

## Purpose

Expose the frozen selected-account fact materialization through one explicit authenticated local
request, so current exposure can later be reviewed without granting Myfxbook execution authority.

## In scope

- One authenticated loopback POST endpoint accepting only `{}`.
- Load the exact saved account ID, validated timezone, and local credential pair.
- Invoke the already-frozen `sync_account` read boundary once.
- Return its canonical account summary, open-position facts, pending-order facts, and bounded recent
  transaction facts with their explicit completeness declaration and observation timestamp.
- Always terminally disconnect before returning success or failure.
- Scripted permanent tests only; no quality-gate request reaches Myfxbook.

## Non-goals and safety

- No automatic request, page-load fetch, polling, persistence, reconciliation, or history.
- No performance series, prediction, signal, trade plan, order placement, cancellation, or mutation.
- No inferred instrument mapping, P/L interpretation, recommendation, or risk override.
- No credentials or session token in responses, errors, logs, or storage.
- No fallback account when the exact saved ID is missing or ambiguous.

## Verification

- Tests prove exact saved-ID binding, canonical serialization, explicit incompleteness, mandatory
  disconnect, redaction, empty-body enforcement, authentication, and fail-closed configuration.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
