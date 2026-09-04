# Phase 20 — Deterministic Paper Portfolio Context

Status: implementation contract; freezing requires the full merge gate and remote verification.
Base: `7fcd1e7945642521f59db0ada9d74f2fe3310e1c`.
Frozen ancestor: `phase19-frozen`, `66772c8a1ba479338699b7c97110523af8daf631`.

## Scope

Build a pure snapshot evaluator for one explicitly identified paper account, one source, and one
valuation currency. Accept a complete caller-supplied account-state receipt and return immutable
position valuations, descriptive exposure, and concentration. Retain pending paper actions without
executing them or changing holdings. This is context for future risk, not risk approval.

The initial arithmetic supports equity and ETF share positions only, with quote currency equal
to account currency. Reject derivatives, contract multipliers, mixed/unknown currencies, and
unsupported asset classes rather than applying an incorrect generic quantity-times-price formula.
Broader asset/account support needs a separate design. No FX conversion, margin model, borrowing
limits, settlement ledger, buying power, liquidation value, or portfolio optimization.

Do not modify frozen `PortfolioPosition` or any Phase 1–19 contract. Its optional unprovenanced
current price is insufficient for this boundary. New types belong in `app/portfolio/models.py`,
with the reference engine in `app/portfolio/engine.py` and explicit exports in `__init__.py`.
Keep `app/data/portfolio.py`, risk, desks, and orchestration unchanged.

## Input contracts

All models inherit immutable, extra-forbidding canonical behavior. Python numeric inputs must
be finite Decimal objects (not floats, integers, booleans, or numeric strings); JSON decimals
are strings decoded without binary floating-point conversion. Preserve input Decimal scale.
Times require aware datetimes in Python and aware ISO strings in JSON, normalize to UTC,
and reject epoch numbers, numeric epoch strings, date-only values, and naive timestamps.

- `ObservedPaperAccountState`: required UUID4 `observation_id` supplied by the caller;
  `account_id` (opaque nonblank case-sensitive string), `source: SourceIdentity`,
  canonical uppercase three-letter `currency`, explicit `effective_at` and `observed_at`,
  signed `cash_balance: Decimal`, `positions` tuple, and `pending_actions` tuple.
  Require effective_at <= observed_at. One receipt asserts the whole account scope, including
  both collections and cash. Empty collections must be explicitly supplied, not defaulted.
  Currency validation is syntactic; no online currency registry lookup.
- `PaperPosition`: full `Instrument`, signed `quantity: Decimal`, and
  `mark: ObservedPositionMark | None`, explicitly supplied. Long, short, and zero quantities
  are legal. Positions are unique and already strictly ordered by the frozen identity key
  (asset class value, exchange or empty string, symbol, currency or empty string).
  Currency must be present and equal to account currency; only EQUITY and ETF are supported.
  No cost basis or profit/loss calculation in this phase.
- `ObservedPositionMark`: required caller-supplied UUID4 receipt identity, explicit source,
  full instrument, nonnegative Decimal price, `valued_at`, and `observed_at`.
  Require valued_at <= observed_at, and complete instrument equality with the position.
  The mark source is retained independently of account source, with no preference/fallback.
  Marks are explicit valuation inputs, not fetched quotes or proof of current liquidity.
- `PendingPaperAction`: required caller-supplied UUID4 `action_id`, full instrument,
  nonzero signed Decimal `remaining_quantity`, `effective_at`, and `observed_at`.
  Positive means proposed net addition to shares; negative means proposed net reduction.
  This is a paper-state assertion, not a broker instruction, approval, or executable order.
  Require effective_at <= observed_at; supported asset class and account currency as above.
  Actions must be unique and already strictly ordered by action_id; repeated instruments
  are legal because distinct pending proposals are not silently netted.
- `PortfolioContextRequest`: required complete account receipt, explicit aware `as_of`
  knowledge cutoff, and explicit aware `evaluation_at`, with as_of <= evaluation_at.
  No hidden default time, generated identity, source, account, currency, or collection.

The account receipt is a caller assertion of complete scope, not a cryptographic completeness
guarantee. Absence of an account receipt is an error, never a flat or zero-cash account.
Negative cash is retained without claiming that borrowing is permissible.

## PIT and snapshot semantics

Account observed_at must be <= as_of. Every mark and pending-action observed_at must also be
<= as_of; pending-action observed_at must additionally be <= account observed_at because it
belongs to the supplied account-state receipt. Marks can be newer than account state: their
independent receipt/effective times remain visible. Never imply all components share one time.

Account effective time, mark valuation times, and pending-action effective times must be
<= evaluation_at. Reject the entire request on any violation, including zero-quantity positions
and empty accounts; do not trim, drop, or repair future-known inputs. Receipt time alone grants
knowledge eligibility; an early economic event never admits a future receipt.

This engine processes one selected receipt, not a history. It does not select latest records,
resolve revisions, reconcile accounts, or deduplicate actions. A later corrected receipt yields
a separate result and cannot mutate an older result. Any history/materialization service is
deferred and must follow the frozen knowledge-order rule.

Require unique mark receipt UUIDs across positions, and distinct account/mark receipt UUIDs.
Action IDs identify proposals and have their own namespace; they are not ingestion receipt IDs.
No staleness threshold is inferred. Future risk must inspect the retained times and define its
own fail-closed freshness policy before using this context.

## Deterministic derived output

`PortfolioContextResult` retains the full validated request, fixed integer schema_version=1,
one `PositionValuation` per input position in unchanged order, and the aggregate fields below.
Each position valuation retains its complete original position, a valuation status, optional
signed market value, optional absolute exposure, and optional gross-exposure share.

Use a fixed fresh Decimal Context with precision 50 and ROUND_HALF_EVEN, independent of caller
precision, rounding, exponent bounds, flags, or traps. Do not use floats or currency quantization.
Finite input arithmetic that overflows or produces a nonfinite result fails explicitly; it must
not become missing data. Retain source values exactly; derived values follow this fixed context.
All sums are left folds in supplied canonical position order, starting at Decimal zero.

| Position case | Status | Signed market value | Absolute exposure |
|---|---|---|---|
| quantity == 0, with or without mark | ZERO_POSITION | 0 | 0 |
| quantity != 0, mark absent | MISSING_MARK | absent | absent |
| quantity != 0, mark present | VALUED | quantity * price | absolute market value |

Validate a supplied zero-position mark normally; zero quantity does not permit invalid evidence.
An explicitly zero mark price is valid evidence and is not a missing mark.

Aggregate `valuation_status` is INCOMPLETE if any position is MISSING_MARK, otherwise COMPLETE.
For INCOMPLETE, net_market_value, gross_exposure, equity_value, and every gross-exposure share
are absent. Keep independently known per-position values, but never label their partial sum as
total portfolio exposure or silently substitute zero for missing marks.

For COMPLETE:

- net_market_value = sum(signed market values).
- gross_exposure = sum(absolute exposures).
- equity_value = cash_balance + net_market_value.
- If gross_exposure > 0, each gross_exposure_share = absolute exposure / gross_exposure.
  These are fractions of gross marked position exposure, not equity weights or risk limits.
- If gross_exposure == 0, all shares are absent with concentration_status=ZERO_GROSS_EXPOSURE.
  Otherwise concentration_status=AVAILABLE. INCOMPLETE valuation instead yields
  concentration_status=INCOMPLETE_VALUATION and absent shares.

Rounded shares need not sum exactly to one; do not adjust a final share to force reconciliation.
Negative or zero equity is retained and does not invalidate the descriptive context. No ratio
uses equity as denominator. Cash is not included in gross position exposure.
For an empty position tuple, totals are zero, equity equals cash, valuation is COMPLETE,
and concentration is ZERO_GROSS_EXPOSURE. This does not imply safe or approved trading.

Pending actions remain in the retained account receipt only. They do not change cash, positions,
marks, exposure, equity, or concentration. Do not estimate fills, reserve buying power, infer
approval, or hide multiple proposals by netting them. Future risk must explicitly account for
pending actions; Phase 20 makes no claim of worst-case post-action exposure.

## Reference engine and validation

`PortfolioContextEngine.evaluate(request) -> PortfolioContextResult` is the protocol.
`DeterministicPortfolioContextEngine` is stateless and has no injected providers or callbacks.
Use shared pure derivation helpers for engine output and direct-result validation so callers
cannot forge totals/statuses by bypassing the engine. Known invalid input raises a chained
`PortfolioContextInvalidInputError`; deterministic arithmetic failure raises a chained
`PortfolioContextComputationError`, both under `PortfolioContextError`.
Unexpected programming errors propagate; never return a successful incomplete result after one.

Reconstruct nested models defensively, including unchecked model_copy/model_construct state.
Require typed immutable tuples in Python and equivalent canonical JSON arrays. Validate exact
request/position correspondence, ordering, cardinality, receipt identities, instrument identity,
status/value consistency, and derived aggregate fields. Provenance comparisons preserve Decimal
scale and every source/time field, not just numeric equality or symbol matching.
Reject extra authority fields such as approved, order, risk_limit, or confidence.

## Non-goals and authority

No provider, broker, network, storage, clock, random IDs, scheduling, environment access,
sorting/repair, source routing, trade execution, fills, settlement, corporate-action processing,
fees, taxes, FX, cost basis, P&L, leverage calculation, recommendation, optimization, or LLM.
Do not reuse source prose or metadata as instructions.

Deterministic risk supremacy remains unchanged: this context does not approve any action.
Future Head-of-Desk defaults to QUIET, and no portfolio valuation can override a risk veto.
Angelo OS may later invoke the typed boundary but cannot replace validated account evidence,
weaken PIT checks, or inject risk approval. No control-plane implementation in this phase.

## Implementation and acceptance gate

1. Add only app/portfolio models, engine, exports, focused tests, and capability documentation.
2. Test long/short/zero positions, signed cash, empty accounts, missing marks, zero marks,
   gross/net/equity arithmetic, zero gross denominator, incomplete aggregate propagation,
   negative/zero equity, multiple proposals, and unchanged valuations with pending actions.
3. Test full instrument identity, currency/asset rejection, strict canonical ordering, duplicate
   IDs, no sorting/netting, exact provenance/Decimal scale, immutability, unknown fields,
   forged nested models, direct-result forgery, and all status/value contradictions.
4. Test PIT equality boundaries, future receipts/events, zero-position future marks, delayed
   marks, future pending receipts relative to account receipt, corrections without mutation,
   timezone normalization, naive/epoch rejection, and Python/JSON round-trip equivalence.
5. Test Decimal precision/rounding/trap independence, nonfinite/float rejection, arithmetic
   overflow failure, and no error-to-missing fallback. No predictive validity is claimed.
6. Audit forbidden dependencies and review scope/severity. Run focused and full tests, Ruff,
   strict mypy, diff checks before implementation commit, then repeat the merge gate.
7. Only then tag/push/verify phase20-frozen and update PROJECT_STATE with the exact merge SHA.

## Design review

Resolved hazards: mixed-currency totals, unsupported contract multipliers, false completeness,
missing marks treated as zero, ambiguous concentration denominator, hidden current-time use,
pending proposals treated as fills, and descriptive equity mistaken for buying power.
Deferred capabilities are explicit and must not be advertised as implemented.
The implementation fixes Decimal exponent bounds at Emin=-999999 and Emax=999999, capitals=1,
clamp=0, empty flags, and explicit InvalidOperation/DivisionByZero/Overflow/Underflow traps.
Inexact underflow fails rather than silently becoming zero exposure. Existing model instances
are revalidated as well as constructor inputs. No frozen upstream contract is modified.
The verified gate and frozen merge SHA are recorded in PROJECT_STATE.md after remote verification.
