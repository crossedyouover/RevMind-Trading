# Phase 52 — Deterministic support/resistance zone design

## Scope

Define descriptive support/resistance zones derived from confirmed Phase 49 pivots. Zones are chart
evidence, not forecasts or trading instructions.

## Explicit configuration

- `half_width`: a strictly positive absolute price distance supplied by policy.
- No hidden ATR, volatility multiplier, tick-size lookup, percentage conversion, or provider rule.
- Configuration is recorded in every result and changing it creates a different evidence context.

## Formation

- A confirmed swing high creates a `RESISTANCE` zone centered on the pivot price.
- A confirmed swing low creates a `SUPPORT` zone centered on the pivot price.
- Lower boundary is `pivot.price - half_width`; upper boundary is `pivot.price + half_width`.
- A negative lower boundary is invalid; it is not silently clamped.
- The zone becomes knowable only at the pivot's `confirmed_at` timestamp.
- Each pivot produces an independent zone. Overlapping zones are not merged in this slice.

## Lifecycle

- `UNTESTED`: no later completed bar intersects the closed zone interval.
- `TESTED`: a later bar range intersects the zone.
- `BROKEN`: a later completed bar closes strictly beyond the far boundary—above resistance or
  below support.
- Test and break timestamps retain the first qualifying bar identity. State moves forward only.
- A break on the same bar as the first test records both events.

## PIT and determinism

- Strictly chronological, single-instrument, single-timeframe canonical input.
- Zone identity derives from pivot identity and explicit width.
- No bar can test or break a zone before pivot confirmation.
- Prefix extension may add forward-only lifecycle events but cannot rewrite formation facts.
- Ordering follows `(confirmed_at, pivot identity)`.

## Exclusions

- No clustering, strength score, touch count ranking, role reversal, volume profile, order block,
  confidence, setup integration, risk change, sizing, alert promotion, or execution.

## Required tests

- support/resistance symmetry;
- unavailable before confirmation;
- closed-interval touch semantics;
- strict close-based break semantics;
- same-bar test and break;
- overlapping zones remain independent;
- invalid width/lower boundary rejection;
- forward-only lifecycle and prefix invariance;
- deterministic identities/serialization;
- mixed, future, duplicate, and unordered input rejection.
