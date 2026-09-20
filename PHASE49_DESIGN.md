# Phase 49 — Deterministic swing structure design

## Scope

Define the first market-structure slice: confirmed swing highs/lows and descriptive break-of-
structure facts over one canonical bar series. This phase freezes semantics before implementation.

## Inputs

- One explicitly ordered canonical bar series.
- One aware evaluation cutoff.
- Explicit integer `left_span >= 1` and `right_span >= 1`.
- No resampling, gap repair, provider fallback, or hidden clock.

## Confirmed pivot semantics

A bar at index `i` is a confirmed swing high only when:

- at least `left_span` earlier and `right_span` later bars exist;
- its high is strictly greater than every high in both comparison windows; and
- the final confirming bar is knowable at the evaluation cutoff.

A confirmed swing low uses the symmetric strictly-lower rule. Equal highs/lows do not create a
pivot. A pivot's `occurred_at` is the pivot bar time; its `confirmed_at` is the final right-window
bar time. Historical selection uses `confirmed_at`, never `occurred_at`, so pivots are never
backdated into an earlier decision context.

## Break-of-structure semantics

After a confirmed pivot exists, a completed bar emits descriptive evidence only when its close:

- closes strictly above the most recent confirmed swing-high price (`BOS_UPWARD`); or
- closes strictly below the most recent confirmed swing-low price (`BOS_DOWNWARD`).

Wicks alone do not count. A level can emit at most one active break fact until a newer confirmed
pivot of the same side replaces it. The fact records the broken pivot identity, break bar identity,
prices, direction, evaluation cutoff, and provenance.

## Ordering and invariants

- Input bars must already be strictly ordered by canonical time and identity.
- Output order is `(confirmed_at, source pivot index, break index, deterministic identity)`.
- Prefix invariance is mandatory: extending a series cannot change evidence already knowable at an
  earlier cutoff.
- Corrections remain separate observation history and are handled by upstream PIT materialization.
- Invalid or insufficient input returns explicit typed non-evidence or raises a validation error;
  it never guesses.

## Exclusions

- No CHOCH classification in this slice.
- No liquidity sweep, order block, fair-value gap, support/resistance zone, or confidence score.
- No BUY/SELL signal, ranking, risk change, position sizing, paper order, or execution authority.
- No chart renderer or browser-owned structure state.

## Required adversarial tests

- insufficient warm-up;
- equal-high/equal-low ties;
- pivot unavailable until the right window closes;
- future bars cannot backdate a pivot or break;
- close-versus-wick distinction;
- one break per pivot level;
- replacement by a newer confirmed pivot;
- prefix invariance across every historical cutoff;
- timezone and strict ordering rejection;
- deterministic serialization and identity.

## Acceptance gate

Implementation may begin only with immutable RevMind-owned models, pure deterministic computation,
and permanent adversarial tests. These facts remain descriptive evidence and cannot influence setup,
risk, planning, alerts, or execution until a later separately validated integration phase.
