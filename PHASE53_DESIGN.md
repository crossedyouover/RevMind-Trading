# Phase 53 — Bounded read-only Myfxbook adapter design

## Scope

Implement the first transport slice that authenticates and converts one selected Myfxbook account
summary into the frozen provider-neutral account snapshot. Open trades, open orders, history,
daily data, dashboard, persistence, credential UI, reconciliation, and execution remain later
independent slices.

## Fixed boundary

- Fixed HTTPS origin: `https://www.myfxbook.com`.
- Injected asynchronous HTTP client and injected UTC clock.
- Login session is secret, IP-bound provider state and never enters canonical models.
- Explicit selected account ID is required.
- Supported calls: login and account list/summary only.
- Broker timezone becomes mandatory in the later trade/history slice where local wall times exist.
- Community outlook is deferred to a later isolated slice.

## Deterministic mapping

- One complete sync receives one `observed_at` after all required payloads validate.
- No provider event timestamps or symbols enter this slice.
- Exactly one matching account identity is required; absent or duplicate identities fail closed.
- The canonical batch declares history `NOT_REQUESTED` and contains no fabricated positions,
  orders, transactions, or performance observations.

## Security and failure behavior

- Credentials and session values use secret types and never appear in repr, logs, models, errors,
  or serialized output.
- Redirects, alternate origins, oversized responses, malformed JSON/schema, provider-declared
  errors, timeouts, authentication rejection, and rate limits become redacted neutral errors.
- The adapter exposes only synchronization and close operations. It imports no broker module and
  contains no order placement, modification, or cancellation method.

## Required tests

- fixed origin and redirect rejection;
- secret redaction through every success and failure surface;
- injected clock called only after complete validation;
- malformed, partial, oversized, absent, and duplicate account payloads;
- deterministic canonical serialization and ordering;
- explicit empty facts and `NOT_REQUESTED` history scope;
- no network in model/protocol tests and no execution/broker dependency.
