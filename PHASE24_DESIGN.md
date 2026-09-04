# Phase 24 — Append-Only Evaluation Journal

Base is phase23-frozen at 403cdac14dc45b7db884f96238994bf6d95be3af (978 tests).
Persist every fully validated Phase 22 disposition, including QUIET and WATCHLIST, with full
inputs/policies/provenance and an explicit recorded_at >= decision evaluation time.
SHA-256 of canonical decision JSON is its stable key. Identical writes are idempotent; the first
recorded_at remains authoritative. An earlier retry timestamp is rejected. No update/delete API.

Record outcomes separately, never mutate decision evidence. Each outcome references a persisted
decision key and retains an explicit ObservedPositionMark, as_of, and recorded_at.
Require decision recorded_at <= as_of <= recorded_at; outcome mark observed_at <= as_of;
mark valued_at >= original decision evaluation; exact instrument equality. Historical journal reads
use recorded_at knowledge and deterministic (recorded_at, key) ordering, not database insertion order.

The first metric is forward reference-price return (outcome price - original proposal reference
price) / original reference price. It is not trade P&L, fill return, expected profit, or a causal
performance estimate. Zero reference price yields UNDEFINED with no value. Use fresh fixed Decimal
context precision 50, ROUND_HALF_EVEN, explicit exponent bounds/traps matching Phase 20.
Negative/zero outcome prices follow canonical mark validation; no future data enters decisions.

SQLite INSERT-only records, canonical JSON with digest verification on every read, explicit close,
atomic single-row insertion, duplicate consistency checks. Unknown decision keys, corrupt payload,
future-known outcomes, early marks, or mismatched identities fail closed. Outcomes hash full
measurement content excluding recorded_at; retries retain the first recorded receipt.
No automated learning, policy mutation, statistical validity claim, transport, provider, or orders.

Tests cover all dispositions, duplicates/reopen, time/currency/instrument boundaries, no future
leakage, outcome arithmetic/zero denominator, multiple outcomes, deterministic retrieval,
database corruption, and caller Decimal-context independence. Full and merge gates before freeze.
