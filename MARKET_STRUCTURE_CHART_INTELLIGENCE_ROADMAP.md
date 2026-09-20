# Market Structure & Chart Intelligence Roadmap

Status: **approved roadmap architecture; not yet an implemented trading signal or execution capability**.

## Purpose

RevMind Trading will add a deterministic market-structure and chart-intelligence layer above the
existing point-in-time market-data/research foundation. The goal is to expose auditable chart
objects and evidence that can later be rendered in the web UI and consumed through the same
governed control plane.

The chart is a presentation of canonical evidence. It is not an independent trading brain.

## Planned deterministic evidence families

Subject to separate design, tests, and historical validation, candidate families include:

- swing structure: higher highs, higher lows, lower highs, lower lows;
- break of structure (BOS) and change of character (CHOCH);
- liquidity levels and liquidity sweeps;
- support and resistance zones;
- supply/demand and order-block candidates;
- fair-value-gap / imbalance candidates;
- trend and volatility context;
- volume anomalies;
- multi-timeframe confluence.

Names such as order block, fair value gap, BOS, CHOCH, and liquidity sweep must receive explicit
RevMind-owned definitions before implementation. No external indicator's opaque definition is
authoritative.

## Intended architecture

```text
PIT market observations
        |
materialized canonical bars
        |
technical + regime evidence
        |
market-structure engine
        |
immutable structure evidence
        |
setup composition / scanner
        |
future portfolio + deterministic risk
        |
future Head of Desk
        |
UI / alerts / governed control plane
```

A future chart renderer may display the same immutable objects as zones, markers, labels, and
overlays. UI state must never become the source of truth for a structure fact.

## Evidence before signals

Candidate outputs should be descriptive and typed, for example:

```text
STRUCTURE_BOS_UPWARD
STRUCTURE_CHOCH_DOWNWARD
LIQUIDITY_SWEEP_LOW
FVG_UPWARD_ACTIVE
FVG_DOWNWARD_ACTIVE
ORDER_BLOCK_UPWARD_ACTIVE
ORDER_BLOCK_DOWNWARD_ACTIVE
```

Exact keys are deliberately **not frozen by this roadmap document**. A dedicated design phase must
define semantics, status lifecycle, provenance, ordering, invalidation, and warm-up behavior before
code is accepted.

These facts must not directly mean BUY or SELL. Any future actionable decision remains downstream
of setup composition, portfolio context, deterministic risk vetoes, and the Head-of-Desk boundary.

## Point-in-time and provenance rules

The future implementation must preserve RevMind Trading's existing knowledge-time guarantees:

1. no structure object may use observations unavailable at the evaluation cutoff;
2. late corrections must not rewrite an earlier historical decision context;
3. pivots or swing points that require future bars must not be backdated as if known earlier;
4. every emitted structure object must retain enough source/provenance information to reproduce
   why it existed;
5. multi-timeframe confluence must use explicit independently knowable series rather than hidden
   resampling or future-complete candles;
6. no silent gap filling, bar repair, deduplication, source blending, or provider preference may be
   introduced inside this layer.

## Chart-intelligence presentation

The future RevMind Trading web app may render:

- candlesticks and trend state;
- structure pivots and BOS/CHOCH markers;
- liquidity levels/sweeps;
- order-block and supply/demand zones;
- fair-value-gap/imbalance zones;
- support/resistance;
- volume and volatility context;
- setup state, invalidation, targets, and deterministic risk information when those downstream
  capabilities exist.

The browser UI, future TradingView adapter, and future Angelo OS adapter should consume the same
versioned application/control interfaces. None may bypass deterministic risk or mutate canonical
evidence.

## Validation requirement

Each structure family must be tested independently before it can influence a setup or decision.
Historical evaluation must use the existing PIT boundaries and compare incremental value against
simple deterministic baselines. Visual plausibility on a chart is not evidence of predictive value.

## Explicit exclusions for the first implementation slice

The first implementation must not add:

- automatic or real-money execution;
- LLM-generated structure facts;
- opaque third-party BUY/SELL signals;
- copied proprietary indicator logic or UI;
- strategy ranking or confidence invented from unvalidated structure concepts;
- broker authority;
- hidden network calls;
- a second market-data truth path outside existing canonical ingestion/replay.

## Implementation sequencing

Implement this capability incrementally after a dedicated adversarial design review:

```text
A. freeze RevMind definitions and PIT semantics
B. immutable structure models + provenance
C. deterministic swing/structure engine
D. deterministic liquidity/imbalance/zone evidence
E. adversarial PIT and prefix-invariance tests
F. controlled integration with evidence/setup composition
G. historical evaluation
H. chart/API rendering
I. optional multi-timeframe confluence
```

The implementation should extend the existing RevMind-owned architecture rather than introducing a
parallel indicator engine.
