# Phase 65 — Selected Myfxbook Account Summary Endpoint

## Purpose

Expose the already-frozen selected-account snapshot adapter through one explicit authenticated local
request so the saved account can later have a truthful, current read-only overview in the dashboard.

## In scope

- One authenticated loopback POST endpoint accepting only `{}`.
- Load the saved validated profile and local credential pair.
- Fetch only the exact saved provider account through the frozen Myfxbook adapter.
- Return the canonical account snapshot: ID, name, currency, balance, equity, optional margin/free
  margin, and actual RevMind observation time.
- Always terminally disconnect before returning success or failure.
- Scripted permanent tests; no quality-gate request reaches Myfxbook.

## Non-goals and safety

- No automatic page-load request, polling, persistence, reconciliation, or history.
- No positions, pending orders, transactions, performance, prediction, signal, or plan.
- No execution method, broker mutation, or real-money authority.
- No credentials or session token in responses, errors, logs, or storage.
- No fallback to a different account when the saved exact ID is absent or duplicated.

## Verification

- Tests prove exact saved-ID binding, canonical response, mandatory disconnect, redaction, empty-body
  enforcement, local-session authentication, and fail-closed missing configuration.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
