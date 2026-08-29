# RevMind Trading

Provider-agnostic AI-assisted market intelligence and paper-trading research platform.

> **Current status: Phase 4 — Ingestion & Observation-Time Foundation. RevMind Trading DOES NOT execute
> trades.**

## Purpose

RevMind Trading is intended to become an AI-assisted market-intelligence, trading-research,
paper-trading, and decision-support system. It will combine deterministic quantitative and
risk controls with configurable specialist AI desks while keeping humans in control.

## High-level architecture

```text
Market Data
→ Quantitative Scanner
→ Technical Structure / Setup Hunter
→ News & Catalyst Intelligence
→ Insider / Flow Intelligence
→ Market Regime Intelligence
→ Portfolio Context
→ Deterministic Risk Engine
→ Head of Desk
→ Alerts
→ Evaluation & Learning
```

Data ingestion, quantitative calculations, portfolio state, AI reasoning, deterministic risk,
alerting, and evaluation are separate concerns. Every AI desk will eventually produce validated
structured data rather than uncontrolled prose.

## Intended desk roles

- **Scanner:** Detect statistically unusual market activity using quantitative code.
- **Hunter:** Analyze technical structure and identify defined setups, triggers, and invalidation
  levels without pretending to predict the future.
- **Catalyst:** Analyze relevant news, filings, earnings, and primary or secondary catalysts.
- **Whales:** Analyze validated insider and flow information, including relevant SEC Form 4 data.
- **Regime:** Determine broader market and sector conditions that may affect individual setups.
- **Portfolio Context:** Track exposure, concentration, and correlations relevant to risk.
- **Risk Engine:** Apply deterministic hard limits, sizing constraints, and veto conditions.
- **Head of Desk:** Combine validated evidence and decide whether anything deserves human attention.

The default system outcome is **QUIET**. The Head of Desk should escalate only when evidence clears
defined thresholds, and duplicate or repeated alerts must eventually be suppressed.

## Non-negotiable architectural rules

1. The initial system has no automatic broker execution.
2. No LLM may directly place, modify, or cancel a trade.
3. Hard risk controls are deterministic software rules.
4. Risk rules never depend exclusively on an LLM judgment.
5. The deterministic Risk Engine can veto any proposed setup.
6. AI providers sit behind a common provider abstraction.
7. OpenAI, Anthropic, and xAI will be interchangeable without rewriting trading-system logic.
8. Data ingestion is separated from AI reasoning.
9. Quantitative calculations are separated from AI reasoning.
10. Portfolio state is separated from AI reasoning.
11. Every AI desk will return validated structured data rather than uncontrolled prose.
12. Every signal will be journaled with its timestamp and source evidence.
13. The system will preserve the information available when a signal was generated to prevent
    look-ahead bias during evaluation.
14. Every signal will be evaluated against subsequent market outcomes.
15. Paper/shadow mode is mandatory before real-money integration is considered.
16. QUIET is the default system state.
17. The Head of Desk escalates only when evidence clears defined thresholds.
18. Duplicate and repeated alerts will be suppressed.
19. Model performance will be measurable independently for GPT, Claude, and Grok.
20. AI performance will be measured against a simpler deterministic or quantitative baseline.

## Provider-independent LLM principle

Future providers will implement a common `LLMProvider` boundary:

```text
LLMProvider
├── OpenAIProvider
├── AnthropicProvider
└── XAIProvider
```

Different providers and models may eventually be assigned to different desks without changing
desk business logic. No LLM provider is connected or implemented during the foundation phase.

## Deterministic risk and paper-first principles

The Risk Engine is deterministic and has veto authority over every proposed setup. An AI judgment
can never be the sole basis for a hard risk decision. Paper/shadow operation and evaluation are
mandatory before any real-money integration is considered. No broker integration exists today.

## Evaluation philosophy

Future evaluation will record, at minimum: signal timestamp and type; instrument; evidence that was
available at generation time; model, provider, and model version where available; confidence;
trigger; invalidation; subsequent price performance; maximum favorable and adverse excursion;
configurable forward outcomes; false-positive rate; and performance by signal type, market regime,
provider/model, and deterministic baseline.

This point-in-time evidence is essential to avoid look-ahead bias and to make every model prove that
it adds value over simpler methods. The evaluation system is not implemented yet.

## Market-data foundation

Market data enters RevMind Trading through a provider-neutral asynchronous boundary. Future
providers must adapt external payloads into canonical `Instrument`, `MarketSnapshot`, and
`MarketBar` models; arbitrary provider dictionaries must never enter downstream components.

The current deterministic fake provider uses only preloaded in-memory canonical objects for tests.
No live data source, broker, trading execution, or external network dependency is connected.

### Event time and observation time

RevMind Trading distinguishes **event time**—when a market or source event occurred—from
**observation time**—when RevMind Trading received or became aware of it. Canonical market-event
models represent event time. The immutable `ObservedMarketData` envelope preserves the canonical
payload, provider-neutral source identity, observation UUID, optional source record ID, and UTC
`observed_at` timestamp.

`observed_at` is the knowledge boundary: a downstream component evaluating time T must never
receive an observation recorded after T. Event time alone does not prove availability. Multiple
sources may independently observe the same event, and repeated observations or revisions are
preserved without reconciliation. The deterministic in-memory observation helper provides only
point-in-time eligibility; it is not a replay engine.

No real provider is connected. Historical replay, backtesting, signal evaluation, broker
integration, and trade execution remain unimplemented.
