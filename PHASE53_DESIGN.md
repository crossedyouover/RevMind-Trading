# Phase 53 — Bounded read-only Myfxbook adapter design

## Scope

Implement only the transport adapter that converts selected Myfxbook account responses into the
frozen provider-neutral external-account facts. No dashboard, persistence, credential UI,
reconciliation engine, or trade execution is included.

## Fixed boundary

- Fixed HTTPS origin: `https://www.myfxbook.com`.
- Injected asynchronous HTTP client and injected UTC clock.
- Login session is secret, IP-bound provider state and never enters canonical models.
- Explicit selected account ID and explicit IANA broker timezone are required.
- Supported calls: account list/summary, open trades, open orders, recent history, and daily data.
- Community outlook is deferred to a later isolated slice.

## Deterministic mapping

- One complete sync receives one `observed_at` after all required payloads validate.
- Provider-local event timestamps are converted with the configured IANA timezone; ambiguous and
  nonexistent wall times fail closed.
- Provider symbols remain unmapped in this slice; no instrument identity is invented.
- Results use stable explicit ordering and reject conflicting duplicate identities.
- History is always labelled `RECENT_INCOMPLETE` because the provider caps it at 50 records.

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
- strict timezone conversion including DST ambiguity/nonexistence rejection;
- malformed, partial, oversized, duplicate, and reordered provider payloads;
- deterministic canonical serialization and ordering;
- 50-record history label and no completeness claim;
- no network in model/protocol tests and no execution/broker dependency.
