# Phase 51 — Deterministic fair-value-gap design

## Scope

Define descriptive three-bar imbalance evidence over one canonical completed-bar series. The term
fair-value gap (FVG) is a RevMind-owned geometric definition here, not a BUY/SELL instruction.

## Formation

For consecutive completed bars `a`, `b`, and `c`:

- `UPWARD` FVG exists when `c.low > a.high`; its lower boundary is `a.high`, upper boundary is
  `c.low`, occurrence time is `c.timestamp`, and provenance includes all three bars.
- `DOWNWARD` FVG exists when `c.high < a.low`; its lower boundary is `c.high`, upper boundary is
  `a.low`, occurrence time is `c.timestamp`.
- Equality creates no gap. The middle bar is retained as provenance but receives no discretionary
  size or momentum requirement in this first slice.

## Lifecycle

- `ACTIVE`: no later completed bar has entered the open interval.
- `PARTIALLY_FILLED`: a later bar enters the interval without reaching the opposite boundary.
- `FILLED`: a later bar reaches or crosses the opposite boundary.
- The first transition time and bar identity are retained. State never moves backward.
- Formation and lifecycle are evaluated only from bars knowable at the explicit cutoff.

## Invariants

- Strictly chronological, single-instrument, single-timeframe canonical input.
- Deterministic identity from instrument, timeframe, three formation timestamps, and direction.
- Prefix invariance: extending history cannot alter previously knowable formation or transition
  facts, except that an active gap may acquire a later forward-only lifecycle transition.
- No gap merging, tolerance, minimum size, resampling, or silent repair.

## Exclusions

- No order-block inference, premium/discount zones, confidence, ranking, signal, risk change,
  sizing, alert promotion, chart authority, or execution.

## Required tests

- upward/downward symmetry;
- equality produces no gap;
- insufficient three-bar warm-up;
- partial and full fill boundaries;
- forward-only lifecycle;
- multiple independent gaps;
- future bars cannot backdate formation;
- deterministic identity/serialization;
- prefix invariance and malformed-input rejection.
