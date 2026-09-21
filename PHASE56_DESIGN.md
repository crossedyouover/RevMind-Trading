# Phase 56 — Myfxbook recent transaction facts design

## Scope

Extend the frozen Myfxbook adapter with the official bounded history response for the selected
account. Materialize at most the provider-documented 50 recent records as immutable
`ExternalTransactionFact` values and set `history_scope=RECENT_INCOMPLETE`.

Performance series, persistence, dashboard UI, reconciliation, sentiment, completeness claims,
and all execution remain excluded.

## Mapping

- Require non-empty unique provider transaction IDs and documented transaction types.
- Convert provider-local event times through the explicit IANA broker timezone; reject ambiguous,
  nonexistent, malformed, or future events.
- Preserve provider symbols and keep instruments unmapped. Non-market cash records may have no
  symbol and no instrument.
- Preserve exact optional quantity, price, and profit/loss Decimals without calculation.
- Reject more than 50 records rather than silently truncating.
- Sort deterministically by `(event_at, provider_record_id)` after complete validation.
- Account, positions, orders, and history share one final `observed_at` boundary.

## Safety and truthfulness

- `RECENT_INCOMPLETE` is mandatory whenever history is requested, including an empty response.
- The result cannot be described as all-time history, a tax ledger, audited performance, or fill
  P&L completeness.
- A malformed history response fails the entire atomic synchronization.
- No trade action, scoring, readiness promotion, risk relaxation, or execution dependency is added.

## Required tests

- 0, 1, 50, and 51-record boundaries;
- missing/duplicate identity, malformed type/time/numeric data, and future-time rejection;
- cash records without symbol and market records remaining explicitly unmapped;
- deterministic ordering, exact Decimal serialization, and one receipt boundary;
- mandatory `RECENT_INCOMPLETE` label and absence of completeness language;
- credential/session redaction and absence of execution methods/dependencies.
