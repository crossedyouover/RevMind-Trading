# Phase 63 — Bounded Myfxbook Connection Test

## Purpose

Let the operator explicitly verify locally stored Myfxbook credentials and view the bounded account
choices returned by the already-frozen read-only adapter. A test is diagnostic, not continuous sync.

## In scope

- One authenticated loopback POST endpoint with no caller-supplied provider parameters.
- Load the validated local credential pair and invoke frozen bounded account discovery.
- Return only redacted account identity/metadata already allowed by the canonical account-choice
  contract; never return credentials or a provider session token.
- Always attempt terminal Myfxbook logout in success and failure paths.
- A Connections-card button and readable test result; no automatic call on page load.
- Explicit failure states for missing/incomplete settings, provider rejection, timeout, malformed
  response, and terminal disconnect failure.

## Non-goals

- No background login, polling, synchronization, reconciliation, portfolio aggregation, or caching.
- No positions, pending orders, transactions, performance series, signals, plans, or execution.
- No automatic selection or mutation of the saved provider account ID.
- No change to PIT processing, deterministic risk veto, news isolation, or Angelo OS boundaries.

## Safety rules

1. The endpoint accepts only an empty JSON body and uses fixed adapter bounds.
2. Credentials and session identifiers never enter HTTP responses, logs, or persisted probe state.
3. Account results are informational choices, never portfolio truth or trade authorization.
4. A provider session must be terminally disconnected before success is reported.
5. Tests use scripted transports only; the quality gate must not contact Myfxbook.
