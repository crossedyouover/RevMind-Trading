# Phase 55 — Myfxbook pending-order facts design

## Scope

Extend the frozen Myfxbook adapter with read-only open-order retrieval for the selected account.
Return immutable `ExternalOpenOrderFact` records in the same atomic synchronization batch.
Transaction history, performance, persistence, dashboard UI, reconciliation, sentiment, and all
execution remain excluded.

## Mapping and lifecycle boundary

- Retrieve only the official `get-open-orders` response through the existing fixed-origin session.
- Require non-empty unique provider order IDs and provider symbols.
- Convert provider-local creation times using the already explicit IANA broker timezone; ambiguous,
  nonexistent, malformed, or future times fail closed.
- Map only documented buy/sell directions. Preserve documented provider order type as descriptive
  text; it grants no execution semantics.
- Preserve exact positive quantity and optional declared price as finite Decimals.
- Symbols remain unmapped (`instrument=None`). No canonical instrument is guessed.
- Sort deterministically by `(created_at, provider_record_id)` after validating the complete batch.
- Account, positions, and orders share the one final `observed_at` receipt boundary.

## Safety

- Pending-order facts are observations only. The adapter has no create, replace, modify, cancel,
  close, mirror, approve, or execution operation.
- Malformed or partial order data fails the entire atomic synchronization; it never returns a
  partial account view.
- Existing credential/session redaction, response-size bounds, fixed origin, and no-broker-import
  constraints remain mandatory.

## Required tests

- exact mapping for buy/sell and documented order types;
- missing, duplicate, malformed, contradictory, and future-dated order rejection;
- DST ambiguity/nonexistence rejection through the frozen timezone boundary;
- deterministic ordering and serialization under reordered provider input;
- exact Decimal precision, unmapped symbols, and one atomic observation boundary;
- proof that failures do not call the receipt clock or return partial facts;
- credential/session redaction and absence of execution methods/dependencies.
