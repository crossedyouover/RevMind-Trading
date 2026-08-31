# RevMind Trading

Provider-agnostic AI-assisted market intelligence and paper-trading research platform.

> **Current status: Phase 6 — Deterministic Historical Replay & Point-in-Time Query Runtime. RevMind Trading DOES NOT execute trades.**

## Purpose

RevMind Trading is intended to become an AI-assisted market-intelligence, trading-research, paper-trading, and decision-support system. Deterministic quantitative and risk controls remain separate from configurable specialist AI desks.

## Non-negotiable architectural rules

- No automatic broker execution exists in the current system.
- No LLM may directly place, modify, or cancel a trade.
- Hard risk controls will be deterministic software rules with veto authority.
- External providers must remain behind RevMind-owned interfaces.
- Historical evaluation must preserve exactly what information was available when a decision was made.
- Paper/shadow operation is mandatory before real-money integration is considered.
- QUIET remains the default future Head-of-Desk outcome.

## Market-data and knowledge-time foundation

External market data must be adapted into canonical `Instrument`, `MarketSnapshot`, and `MarketBar` models before downstream use. The current fake provider is deterministic and offline; no live market provider or broker is connected.

RevMind Trading distinguishes **event time** from **observation time**. Event time (`payload.timestamp`) records when the market/source event occurred. `ObservedMarketData.observed_at` records when RevMind Trading received or became aware of that information.

`observed_at` is the historical knowledge boundary. A component evaluating time T may only receive observations satisfying `observed_at <= T`; an earlier event time never grants early visibility. Repeated receipts, corrections, and observations from multiple providers remain distinct historical facts rather than being silently reconciled.

## Durable observation storage

Phase 5 established the append-only SQLite observation store. Canonical `ObservedMarketData` JSON is authoritative; projected columns support queries and integrity checks. Exact integer UTC microseconds are used for time projections. Duplicate observation UUIDs are rejected while repeated source records and multi-provider observations remain valid history.

## Deterministic historical replay

Phase 6 adds a separate historical read/replay boundary above the append-only store. `SQLiteHistoricalObservationReader` exposes immutable canonical observations through cursor-based batches without turning the persistence interface into a generic repository.

Historical eligibility is based only on `observed_at <= as_of`. The canonical knowledge order is:

```text
observed_at ASC
observation_id ASC
```

`ReplayCursor` uses exactly that pair for deterministic continuation. Replay never relies on SQLite row order, `rowid`, physical insertion order, event time, source, or offset pagination.

A historical reader owns a fixed SQLite read transaction for its lifecycle. Together with a caller-supplied `as_of` cutoff, this prevents commits made after the replay snapshot begins from appearing partway through an active replay. A newly opened reader may see those later commits when they satisfy its cutoff.

Batch reads default to 1,000 observations and are bounded at 10,000. Empty and exhausted batches are explicit, and continuation cursors are returned only when another eligible batch remains.

Phase 6 introduces no replay clock or event scheduler. Future deterministic consumers can treat each delivered observation's `observed_at` as the effective knowledge clock; richer simulated-time behavior belongs to a later event runtime.

## Future architecture

The intended flow remains:

```text
Market Data
→ Historical / Point-in-Time Data Boundary
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

No real provider, indicator engine, strategy engine, backtesting engine, LLM intelligence, portfolio optimizer, execution integration, REST/WebSocket control plane, web UI, or Angelo OS integration is implemented yet.
