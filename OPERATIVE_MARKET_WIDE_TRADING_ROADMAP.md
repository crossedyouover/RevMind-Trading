# Operative Market-Wide Trading Roadmap

Status: approved end-state requirement. This document does not itself enable live-money execution.

## Product requirement

RevMind Trading must evolve from an on-demand research desk into a continuously operating,
market-wide trading system. It must be able to discover opportunities rather than requiring a user
to supply a chart, and it must eventually manage the complete governed order lifecycle.

The target loop is:

```text
market universe
-> eligibility / data-quality / liquidity filters
-> deterministic scanner
-> opportunity ranking
-> deep evidence enrichment
-> portfolio context
-> deterministic risk veto
-> Head of Desk decision
-> order intent
-> execution gateway
-> broker/exchange adapter
-> fill/reconciliation monitor
-> position management
-> exit
-> journal / P&L / evaluation
```

## Market-wide opportunity engine

The system should continuously evaluate hundreds or, where infrastructure permits, thousands of
instruments. Expensive analysis must be funnelled rather than applied blindly to every instrument.

Example funnel:

```text
416 monitored
-> 180 eligible/liquid
-> 30 deterministic setups
-> 10 high-interest candidates
-> 4 contextually confirmed
-> 2 pass portfolio/risk
-> 0..N approved order intents
```

Counts are illustrative, not strategy thresholds.

The opportunity layer may eventually consider liquidity, spread, market depth, volume, volatility,
technical/market structure, regime, catalysts, derivatives positioning, flows and other validated
evidence. Liquidity or depth describes executability; it is not automatically directional alpha.

## Operative execution requirement

A complete operative system must support more than order submission:

- construct a typed order intent from an approved decision;
- validate instrument, side, quantity, price constraints and trading mode;
- enforce portfolio/risk limits again at the execution boundary;
- submit, modify and cancel only through an approved adapter;
- handle acknowledgements, rejection, partial fills and fills;
- reconcile local state against broker/exchange state;
- monitor open positions and protective orders;
- enforce bounded stop/exit policy;
- recover safely from process/network interruption;
- journal every decision, order transition, fill and exit;
- compute realized/unrealized outcomes from reconciled execution facts;
- provide an immediate kill switch.

No LLM or external agent may possess unrestricted execution authority.

## Authority progression

Execution authority must advance only through explicit modes:

```text
BACKTEST
-> PAPER
-> SHADOW
-> SUPERVISED_LIVE
-> BOUNDED_AUTONOMY
```

SUPERVISED_LIVE means the system discovers and constructs a live order but requires explicit
one-time user approval.

BOUNDED_AUTONOMY may execute without a click only inside explicit policy limits such as approved
instruments/venues, maximum order and portfolio exposure, maximum daily loss, position count,
slippage/price bounds, market-hours rules and kill-switch state.

No phase transition is implicit.

## Free-subscription constraint

RevMind Trading must not require a paid software, data, API, MCP, model or infrastructure
subscription for a capability to operate.

Preferred dependencies are:

- free/open-source/self-hosted software;
- public or genuinely free APIs with usable perpetual free access;
- broker/exchange APIs available without a software/data subscription;
- locally runnable models where practical.

Freemium providers may be used only for capabilities genuinely available on the free tier. Paid-only
features may be documented as benchmarks but must not become implementation dependencies.

Normal market costs such as commissions, exchange fees, spreads, slippage and trading capital are
not software/data subscriptions, but must remain explicit in evaluation and P&L.

## Model/agent boundary

LLMs and specialist models may research, summarize, critique, compare or propose. They must not
bypass deterministic gates.

```text
models / agents
      |
proposed analysis or decision
      |
canonical validation
      |
portfolio context
      |
DETERMINISTIC RISK VETO
      |
Head of Desk policy
      |
execution policy
      |
broker/exchange adapter
```

Broker credentials must remain isolated behind the execution adapter/control boundary.

## Operational safety

Before bounded autonomy, the implementation must prove:

- idempotent order-intent handling;
- duplicate-order prevention;
- stale-data rejection;
- deterministic risk revalidation immediately before submission;
- explicit clock/session semantics;
- broker/exchange reconciliation;
- restart/crash recovery;
- partial-fill handling;
- orphaned-order detection;
- position/order consistency checks;
- bounded retry policy;
- audit log integrity;
- kill switch and fail-closed behavior;
- maximum-loss and exposure enforcement;
- safe behavior when data, provider, model or broker connectivity is degraded.

An inability to establish current authoritative execution state must fail closed for new autonomous
risk.

## UI / control plane

The future Command Center should expose at minimum:

- markets monitored;
- eligible universe;
- current setups/candidates;
- ranked opportunities and evidence;
- risk-vetoed candidates and reasons;
- open/pending orders;
- positions and protective orders;
- realized/unrealized P&L;
- provider/data health;
- trading authority mode;
- kill-switch state;
- decision/order/fill audit trail.

The browser UI and future Angelo OS/Jarvis adapter must call the same governed application
capabilities. Neither receives a privileged bypass around risk or execution policy.

## Validation gates

Market-wide operation and autonomous execution require separate evidence of correctness.

Before live authority, evaluate:

1. PIT-safe historical performance including costs/slippage assumptions;
2. paper/shadow behavior over sustained market periods;
3. scanner throughput and missed-data behavior;
4. opportunity-ranking stability;
5. broker/exchange adapter conformance;
6. reconciliation and restart scenarios;
7. simulated rejected/partial/duplicate/out-of-order execution events;
8. portfolio/risk invariants under concurrent candidates;
9. kill-switch and connectivity failures;
10. forward supervised-live results before bounded autonomy.

Reported social-media trading results are not validation evidence.

## Implementation sequence

```text
A. market-universe + eligibility contracts
B. scalable market-wide scanner orchestration
C. deterministic opportunity scoring/ranking
D. enrichment funnel and portfolio-aware candidate selection
E. sustained paper/shadow operation
F. typed order-intent + execution-state contracts
G. broker/exchange execution adapter boundary
H. fill/reconciliation + position-management engine
I. supervised-live authority mode
J. extended operational validation
K. bounded-autonomy policy + kill switch
L. bounded live operation only after all prior gates pass
```

The existing on-demand Alpaca paper-order path is a useful boundary test, not the final operative
architecture. Continuous discovery, autonomous order placement, reconciliation, position management
and live-money authority remain separate future milestones.
