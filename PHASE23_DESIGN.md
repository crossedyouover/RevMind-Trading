# Phase 23 — Durable Provider-Neutral Alert Outbox

Implementation contract. Prior phases stay frozen. No live messaging adapter or credentials.

Accept only fully revalidated Phase 22 ALERT results, an explicit destination and expiry.
Retain the complete decision. A deterministic SHA-256 key covers canonical decision JSON and
destination; expiry is retained but cannot change an existing key's immutable payload.
QUIET/WATCHLIST are invalid delivery inputs. Destination authority is an explicit coordinator
allowlist and must be checked on every call, including duplicates.

The injected transport receives only the validated envelope. The supplied reference transport
is a local recording sink, not Telegram/email/webhook delivery. No broker or order interface.
Future network adapters require separate authorization and must respect these outcome semantics.

SQLite outbox stores immutable envelope JSON and append-only attempt events. BEGIN IMMEDIATE
serializes claims across connections. Commit STARTED before invoking transport, never hold the
database transaction over external work. A second dispatch of STARTED/DELIVERED/UNCERTAIN/EXPIRED
does not send again. STARTED after a crash is unresolved and must not auto-retry.

Transport returns DELIVERED or DEFINITELY_NOT_SENT. An exception or malformed receipt is UNCERTAIN.
DEFINITELY_NOT_SENT may retry only on an explicit retry_failed=True call; all other duplicates
return the durable record. No exactly-once external delivery claim: a crash after remote acceptance
but before final persistence remains STARTED. No automatic retries of uncertain outcomes, fallback,
destination rewriting, or source-text interpretation. There is no manual resolve-to-retry bypass.

Caller supplies aware attempted_at; require attempted_at >= decision evaluation time and >= prior
attempt time. Expired means attempted_at > expires_at, with no transport call. Expiry must be >=
decision evaluation. Canonical UTC times, explicit tuples, immutable models, strict retry boolean.
Validate stored JSON/key/state on reads. Payload collision fails closed. Database errors propagate,
including outcome persistence failure, and leave the pre-send claim durable.

Tests: only ALERT eligibility, allowlist, canonical content collision, duplicate/reopen recovery,
definite-failure explicit retry, uncertain exception, malformed receipt, expiry/time boundaries,
concurrent claims, append-only event history, database failure, and no frozen module changes.
Full test/lint/type/diff and merge gates precede tag/push. This is delivery infrastructure, not
proof of successful delivery to a real service or exactly-once transport.
