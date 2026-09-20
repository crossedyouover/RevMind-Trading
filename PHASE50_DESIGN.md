# Phase 50 — Deterministic liquidity-sweep design

## Scope

Define descriptive liquidity-level and sweep evidence derived only from Phase 49 confirmed swing
pivots and canonical completed bars. This phase freezes semantics before implementation.

## Liquidity levels

- A confirmed swing high creates an `ABOVE_HIGH` liquidity level at its exact high price.
- A confirmed swing low creates a `BELOW_LOW` liquidity level at its exact low price.
- The level becomes knowable only at the pivot's `confirmed_at` time.
- Equal or approximately equal levels are not clustered in this slice. Every pivot remains an
  independent, provenance-preserving level.
- A level retains the source pivot identity, instrument, timeframe, price, occurrence time,
  confirmation time, and deterministic identity.

## Sweep semantics

An upward-side sweep occurs when a later completed bar:

1. trades strictly above an active `ABOVE_HIGH` level with its high; and
2. closes at or below that level.

A downward-side sweep occurs symmetrically when a later completed bar trades strictly below an
active `BELOW_LOW` level and closes at or above it.

A close through the level is not a sweep. Phase 49 may classify that separately as descriptive
break-of-structure evidence. One level emits at most one sweep fact.

## PIT and lifecycle rules

- A bar cannot interact with a level before the source pivot is confirmed.
- `occurred_at` is the sweep bar time; `observed/evaluated_at` is the explicit evaluation cutoff.
- Extending a series cannot alter sweep facts knowable at an earlier cutoff.
- A swept level remains historically swept; it is not silently reactivated.
- New pivots create new independent levels, even at the same price.
- Input must be canonical, strictly chronological, single-instrument, and single-timeframe.

## Outputs

- Immutable liquidity-level facts.
- Immutable upward-side or downward-side sweep facts.
- Exact source pivot and sweep-bar provenance.
- Deterministic ordering and identities.

## Exclusions

- No tolerance-based equal-high/equal-low clustering.
- No liquidity pools, volume profile, order-book depth, stops, or broker data.
- No CHOCH, order block, fair-value gap, confidence score, BUY/SELL signal, ranking, risk change,
  sizing, alert promotion, or execution authority.

## Required tests

- level unavailable before pivot confirmation;
- wick beyond plus close back inside emits one sweep;
- close through does not emit a sweep;
- touch without strict penetration does not emit a sweep;
- one sweep per independent level;
- identical-price pivots remain independent;
- upward/downward symmetry;
- prefix invariance at every cutoff;
- deterministic serialization and identity;
- rejection of mixed, future, duplicate, or unordered inputs.

## Acceptance

Implementation must remain a pure deterministic evidence layer consuming frozen Phase 49 outputs.
No downstream trading behavior changes in this phase.
