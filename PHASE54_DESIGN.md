# Phase 54 — Myfxbook open-position facts design

## Scope

Extend the frozen Myfxbook adapter with read-only open-trade retrieval for one selected account.
Return immutable `ExternalOpenPositionFact` records in the same atomic synchronization batch.
Pending orders, transaction history, performance series, persistence, dashboard UI, reconciliation,
sentiment, and all execution remain excluded.

## Time and identity

- Configuration requires an explicit valid IANA broker timezone.
- Provider-local open times are converted to UTC without consulting the machine timezone.
- Ambiguous and nonexistent daylight-saving wall times fail closed; no fold is guessed.
- Receipt time is assigned once, after the account and complete position response validate.
- Provider trade IDs must be non-empty and unique. Conflicting or repeated IDs fail closed.
- Provider symbols remain explicit and unmapped (`instrument=None`) in this phase.
- Output ordering is deterministic by `(opened_at, provider_record_id)`; unordered provider input
  is accepted only because the adapter explicitly canonicalizes the complete response.

## Mapping

- Direction maps only documented buy/sell values to `LONG`/`SHORT`; unknown values fail closed.
- Volume and prices use exact finite decimals and must satisfy canonical constraints.
- Optional current price and unrealized result remain absent unless explicitly documented and
  validated; no value is calculated or inferred.
- Account and every position share one provider/account identity and `observed_at` boundary.

## Security and authority

- Reuse the fixed-origin, bounded-response, redacted-error, secret-session transport.
- Open positions are external observations, not instructions. They cannot place, modify, close,
  mirror, size, approve, or promote a trade.
- No broker or execution module dependency is permitted.

## Required tests

- timezone conversion across ordinary and offset-changing dates;
- ambiguous/nonexistent/invalid timezone rejection;
- future open-time rejection relative to the final receipt boundary;
- missing, malformed, duplicate, and contradictory trade records;
- exact Decimal preservation and deterministic ordering/serialization;
- unmapped symbol isolation and one atomic observation boundary;
- credential/session redaction and no execution surface.
