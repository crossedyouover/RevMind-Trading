# Phase 21 — Deterministic Paper Risk Gate

Status: implementation contract; freeze requires the full merge gate and remote verification.
Base: `afddaad633acabc9ec6479cf0492bfbc8720b0dc`.
Frozen ancestor: `phase20-frozen`, `0fd80af25b2ffb69a1b3c38cca825aac91202939`.

## Scope and authority

Evaluate one explicitly supplied hypothetical paper share change against an immutable Phase 20
context and explicit policy. Produce either VETO or PASS_CHECKS. PASS_CHECKS means only that this
version's enumerated checks passed for these exact inputs; it is not an executable approval,
buying-power assertion, prediction, recommendation, or authorization to trade.

Deterministic veto is unconditional. Desks, LLMs, confidence, and future Angelo OS controls cannot
override it. No real execution, paper fill, reservation, account mutation, or Head-of-Desk behavior.
QUIET remains the future Head-of-Desk default. Any error or unavailable result is non-passing.

Scope follows Phase 20: one paper account, one currency, equity/ETF shares. This first gate rejects
all contexts containing pending paper actions; it does not model simultaneous fills or silently
net proposals. Modeling pending exposure needs a separately designed extension, not a bypass flag.
Reducing or closing a position does not exempt a request from freshness, completeness, or policy.

## Required contracts

- `PaperRiskProposal`: explicit caller-supplied UUID4 proposal_id, opaque account_id,
  full Instrument, nonzero signed Decimal quantity_change, aware effective_at and observed_at,
  and an explicit `ObservedPositionMark` reference_mark from Phase 20.
  Require effective_at <= observed_at; reference mark instrument must match exactly.
  The proposal is research input, not an order. No generated IDs or default quantity/price.
- `PaperRiskPolicy`: nonblank policy_id and policy_version; exact account_id and uppercase
  currency binding; positive Decimal max_abs_quantity_change, max_proposal_notional,
  max_gross_exposure, max_instrument_exposure; positive max_gross_exposure_share <= 1;
  strict boolean allow_short_positions; strictly positive Decimal min_equity_value;
  nonnegative Decimal min_cash_balance; strict nonnegative integer max_account_age_us,
  max_mark_age_us, max_proposal_age_us. All fields required, with no numerical defaults.
  Values are explicit caller policy, not investment advice or learned settings.
- `PaperRiskRequest`: complete `PortfolioContextResult`, proposal, policy, explicit aware
  as_of and evaluation_at, with as_of <= evaluation_at. No optional context or implicit policy.
- `PaperRiskResult`: full validated request, schema_version literal integer 1, derived status,
  canonical tuple of reason codes, and optional complete `PaperRiskProjection`.
  Projection retains every post-change position identity/quantity/value in canonical order,
  projected cash, gross exposure, and per-instrument gross-exposure fractions.
  A projection is a hypothetical calculation, not an account-state receipt.

Use immutable extra-forbidding models, finite strict Decimal inputs and string JSON decimals,
strict aware times, typed Python tuples, canonical JSON round trips, and defensive nested
revalidation including already-constructed and unchecked copied instances. Do not reuse the
legacy RiskDecision model with its generated identity and differently scoped status semantics.
Leave all frozen Phase 1–20 contracts unchanged.

## Structural validation versus risk veto

Wrong types, missing fields, invalid policy bounds, malformed identities, zero proposal quantity,
unsupported asset class/currency syntax, forged context, negative reference prices, or impossible
time relationships fail input validation. They never yield PASS_CHECKS or a fabricated empty
projection. Account/source authenticity and completeness remain upstream trust assumptions.

Well-formed but unsuitable context produces VETO with deterministic reason codes. This includes
account/currency binding mismatch, future-known inputs, stale evidence, pending actions,
incomplete valuations, nonpositive marks for nonzero holdings/proposal, insufficient current
equity, and failure of configured exposure/cash/short-position limits.

## PIT and freshness prerequisite gate

Require context.request.as_of <= risk.as_of and context.request.evaluation_at <= risk.evaluation_at.
Require proposal.observed_at and reference_mark.observed_at <= risk.as_of.
Frozen context revalidation already bounds its component receipts by its own cutoff; retain all
original times. No resetting an old receipt time when wrapping it in a new risk request.

Freshness is measured from risk evaluation_at to effective/valuation time, not merely receipt:
account effective_at for account age, proposal effective_at for proposal age, each held nonzero
position's mark valued_at and the proposal reference mark valued_at for mark age.
Compute integer microseconds exactly with timedelta days/seconds/microseconds, never total_seconds
float. An age equal to the configured maximum passes; strictly greater is stale. A future event
never passes: check all retained account/mark/action/proposal event times against evaluation_at.
Zero-position marks remain PIT-validated even though mark-age checks concern nonzero holdings.

Proposal account, portfolio account, and policy account must match case-sensitively; policy
currency must equal account and proposal instrument currency. Match the full instrument identity,
never symbol alone. If a matching nonzero holding exists, reference_mark must equal its retained
mark in complete canonical serialization (including Decimal scale and receipt/source identity).
Do not reprice an existing holding by silently replacing the mark. An absent or zero holding may
use the explicitly supplied proposal mark; its own freshness and knowledge rules still apply.

Any pending action vetoes; retain the complete tuple without estimating reservations.
Incomplete valuation vetoes. Any nonzero held position with mark price <= 0 vetoes, and the
proposal reference price must be strictly positive. Phase 20 legitimately accepts zero marks;
this stricter risk eligibility rule does not alter that frozen descriptive contract.

Require current context equity_value >= min_equity_value. Equity remains descriptive and is not
buying power. Current cash need not satisfy min_cash_balance before a hypothetical reduction,
but projected cash must satisfy it. This is a simple paper cash floor, not settlement accounting.

## Deterministic hypothetical projection

Only calculate projection when every prerequisite passes. Otherwise projection is absent.
Use a fresh explicitly configured Decimal context identical in settings to Phase 20: precision
50, ROUND_HALF_EVEN, Emin=-999999, Emax=999999, capitals=1, clamp=0, empty flags, and traps for
InvalidOperation, DivisionByZero, Overflow, Underflow. Do not import its private helpers or change
its engine. Caller context and mutable DefaultContext must not affect results.

At the proposal instrument, projected quantity = held quantity (or zero) + quantity_change.
Retain all existing positions, including resulting zeros. If absent, insert exactly one new
position at its canonical identity position; this explicitly specified construction is not
repair/sorting of malformed input. Other positions remain unchanged with their original marks.
Price the changed position using the exact proposal reference mark; other positions retain marks.

proposal_notional = abs(quantity_change * reference_price).
projected_cash = current cash_balance - quantity_change * reference_price.
Projected market values are projected quantities times their assigned marks; zero positions
have zero value even without marks. Sum absolute projected values in canonical identity order
to obtain projected gross exposure. No fees, slippage, FX, tax, settlement, borrow availability,
or partial fills. Never claim this estimate is worst-case loss or achievable execution.

If projected gross > 0, each instrument share is absolute projected value / projected gross;
otherwise every share is absent and concentration is explicitly ZERO_GROSS_EXPOSURE.
Do not divide by equity or force rounded shares to sum exactly to one.
Retain complete projection even when a subsequent numerical limit vetoes it.

Apply all these limits, collecting every breach:

1. abs(quantity_change) <= max_abs_quantity_change.
2. proposal_notional <= max_proposal_notional.
3. projected_cash >= min_cash_balance.
4. projected gross exposure <= max_gross_exposure.
5. Every projected instrument absolute exposure <= max_instrument_exposure.
6. Every available projected gross-exposure share <= max_gross_exposure_share.
7. If allow_short_positions is false, every projected quantity must be >= 0.

Equality passes all bounds. A zero-gross projection passes the concentration ceiling without
inventing a numerical share. Check the entire projected account, not just the proposed instrument:
an existing breach elsewhere may remain a veto. No auto-resizing, clipping, alternative proposal,
partial acceptance, or implied special approval for a risk-reducing trade.

## Stable reason codes and result validation

Prerequisite codes, in fixed order:
ACCOUNT_MISMATCH, CURRENCY_MISMATCH, FUTURE_KNOWLEDGE, FUTURE_EVENT,
STALE_ACCOUNT, STALE_PROPOSAL, STALE_MARK, PENDING_ACTIONS,
INCOMPLETE_VALUATION, NONPOSITIVE_MARK, REFERENCE_MARK_MISMATCH, EQUITY_BELOW_MINIMUM.

Evaluate independent prerequisite checks even if others fail. Dependent checks skip unavailable
operands (for example equity comparison when valuation is incomplete); absence is already vetoed.
Emit each applicable code once, irrespective of how many instruments violate it.
If any prerequisite code exists, return VETO and no projection; do not append projection codes.

Projection codes, in fixed order:
QUANTITY_LIMIT, PROPOSAL_NOTIONAL_LIMIT, CASH_FLOOR, GROSS_EXPOSURE_LIMIT,
INSTRUMENT_EXPOSURE_LIMIT, CONCENTRATION_LIMIT, SHORT_POSITION_DISALLOWED.
If none occurs, status is PASS_CHECKS and reasons is an empty tuple.
Otherwise status is VETO with all applicable ordered codes and the retained complete projection.

Direct result construction must recompute status, reasons, and projection using the same pure
helpers as the engine. Reject forged passes, omitted/duplicate/reordered reasons, partial
projections, changed provenance, and authority fields. Strict schema_version excludes bool/float.
No fail-open optional flags, override tokens, confidence fields, or advisory text.

## Engine, errors, and non-goals

Add typed models and pure helpers in app/risk/models.py; implement the existing engine placeholder
as `DeterministicPaperRiskEngine.evaluate(request)`, implementing `PaperRiskEngine`.
Export public contracts through app/risk/__init__.py.
Known malformed input raises chained PaperRiskInvalidInputError. Arithmetic failure raises chained
PaperRiskComputationError, both under PaperRiskError. Unexpected programming errors propagate.
No exception becomes PASS_CHECKS; consumers must treat every exception as unavailable/non-passing.

No I/O, providers, clocks, randomness, LLM, alert delivery, account persistence, runtime policy
registry, automatic policy updates, broker/paper execution, orchestration, or Angelo OS integration.
This version checks exposure and cash proxies, not comprehensive financial risk: gap loss,
liquidity, volatility, stop-loss loss estimates, borrow constraints, and concurrent proposals remain
unmodeled. Production or real-money suitability is not claimed.

## Acceptance gate

Test exact-bound passes and one-unit/microsecond breaches for every policy field; strict types,
zero/negative/nonfinite/float rejection; all prerequisite codes and deterministic multi-failure
ordering; pending-action veto regardless of direction; incomplete/zero-mark contexts; account and
full identity mismatch; exact existing-mark provenance; new instruments and canonical insertion;
long/short/flat projections; negative cash with reductions; empty accounts; whole-account limits;
zero gross concentration; unchanged input history; corrections and future/stale boundaries.

Test direct-result forgery, Python/JSON parity, nested unchecked copies, Decimal scale retention,
caller precision/traps/exponent/default-context independence, overflow/underflow error behavior,
no clipping or override, and forbidden dependency/call audits. Use no real credentials or providers.
Review severity/scope, run focused/full pytest, Ruff, strict mypy, and diff checks before commit;
repeat after merge before tagging/pushing/verifying phase21-frozen and updating PROJECT_STATE.

Design review resolves pending-action ambiguity by veto, makes every threshold caller-supplied,
and separates passing a bounded paper check from execution authority. Policy version identifies
caller-supplied configuration; this boundary does not prove that policy was historically deployed
or manage a policy registry. Production callers must preserve the exact policy with every result.
The implementation adds no real-money suitability claim. Verification and the frozen merge SHA
are recorded in PROJECT_STATE.md after successful remote verification.
