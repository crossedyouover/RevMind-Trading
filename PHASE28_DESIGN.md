# Phase 28 — Provider-Neutral Operations and Local Market Data

Status: design approved for implementation on `codex/phase28-provider-operations` from frozen
Phase 27 SHA `02c41a377f91323f6a8a304f09d79dbbb49c05b4`. This document grants no live-money,
automatic-execution, scraping, or unrestricted network authority.

## Objective

Make RevMind practically useful beyond Alpaca without weakening the frozen point-in-time,
deterministic analysis, or risk boundaries. The dashboard must distinguish three independent
roles instead of treating one provider as the application:

1. **Market data** supplies timestamped canonical bars for research.
2. **Paper broker** supplies a paper account and accepts only explicitly approved paper orders.
3. **News context** supplies factual timestamped headlines that never affect readiness or orders.

Phase 28 begins with a provider-neutral local bar-import lane. A user may export historical bars
from a lawful source such as a charting platform or broker and analyze them locally. Alpaca remains
the only implemented network market-data and paper-broker adapter. No unsupported provider is
shown as connected, and a catalog selection never implies that data exist.

## Non-negotiable inherited constraints

- Event time and RevMind receipt time remain distinct. Imported rows receive an actual import
  receipt boundary; their historical timestamps are never reused as knowledge time.
- Analysis consumes a sealed, canonical, bounded input set. Rows are validated before persistence;
  malformed, duplicate, unordered, future, incomplete, or mixed-identity input fails closed.
- Deterministic research and deterministic risk retain supremacy. News, provider labels, UI state,
  LLM text, and user optimism cannot promote readiness or override a veto.
- Provider wire formats terminate at adapters. Downstream research receives only RevMind-owned
  `Instrument`, `MarketBar`, and observation contracts.
- Local import grants research capability only. It cannot create broker authority, synchronize an
  account, submit an order, or make an imported symbol tradable through Alpaca.
- Automatic and real-money execution remain absent. Alpaca paper orders retain exact explicit
  confirmation, server-enforced READY eligibility, expiry, account synchronization, and risk veto.
- Contracts remain strict and versioned for future authenticated Angelo OS orchestration.

## First implementation slice: bounded CSV bar import

Add a dedicated **Import data** connection option and dashboard workflow. The accepted UTF-8 CSV
schema is exact and documented:

```text
timestamp,open,high,low,close,volume
2026-09-11T14:30:00Z,100.00,101.00,99.50,100.75,12500
```

Identity and market semantics are supplied separately and explicitly:

- symbol;
- canonical asset class (`EQUITY`, `ETF`, `CRYPTO`, `INDEX`, `FX`, `FUTURE`, `OPTION`, `OTHER`);
- optional exchange or venue;
- optional quote currency;
- one supported fixed timeframe;
- source label identifying the export origin;
- declared timezone policy, initially UTC timestamps with offsets required.

The endpoint accepts at most one instrument and one timeframe per request, a bounded payload and a
bounded row count. Headers are exact, values are strict finite decimals, OHLC relationships use the
canonical model, timestamps are aware UTC-normalized and strictly increasing, and duplicate bar
timestamps are rejected. The server captures one actual receipt time after parsing and before the
atomic observation append. No row is silently repaired, sorted, deduplicated, forward-filled,
timezone-guessed, or adjusted.

Imported history is analyzed through the existing materialization/research/trend/evaluation path.
Readiness retains the Phase 27 freshness gate, so an old export can explain historical evidence but
cannot appear currently READY. Imported results may produce a hypothetical risk plan only when all
existing server-side gates pass, but they cannot be submitted to any broker because provider/broker
instrument binding has not been proven.

## Dashboard interaction model

The Connections page must show independent capability cards:

- **Alpaca market data** — connected, needs credentials, failed, or not configured.
- **Local CSV bars** — always locally available for supported canonical instruments.
- **Alpaca Paper Trading** — separately connected or unavailable.
- **Public/Alpaca news** — context-only status, visibly isolated from analysis authority.

The primary workflow becomes:

```text
Choose market -> choose a real data lane -> validate data -> analyze -> inspect blockers
              -> risk plan when READY -> explicit paper approval only when broker-bound
```

Every result identifies source, exact instrument, timeframe, bar count, newest event time, receipt
time, freshness, and whether paper execution is unavailable. The UI must use plain-language actions
and keep advanced evidence behind disclosure controls.

## Persistence and recovery

Use the existing append-only observation store and frozen ingestion semantics. Store an immutable
import receipt containing schema version, identity, source, timeframe, row count, first/last event
time, received-at value, and canonical content digest. Repeating the same content is a new truthful
receipt unless an explicit request ID is later introduced; it must never overwrite prior knowledge.

Partial parsing or persistence failure produces no successful import receipt. Existing committed
observations are never deleted by a retry. Imported source labels and filenames are metadata only;
paths and file contents must not enter logs, news context, or execution requests.

## Acceptance tests

- Exact CSV success for every canonical asset class and supported timeframe.
- Missing/extra headers, blank fields, non-UTF-8, oversized input, excessive rows, nonfinite or
  negative values, invalid OHLC, naive timestamps, non-increasing timestamps, duplicates, mixed
  identity attempts, and future bars all fail closed.
- Actual receipt time differs from event time and governs point-in-time eligibility.
- Atomic observation append and immutable import receipt survive restart.
- Analysis uses the same deterministic pipeline and produces identical output from identical sealed
  inputs; old imports are blocked by freshness rather than promoted.
- Imported data cannot access paper account/order endpoints or acquire an Alpaca instrument binding.
- News content cannot change imported research, readiness, plan, risk, or execution eligibility.
- Dashboard states and errors contain no credentials, local paths, provider payloads, or stack traces.
- Focused and complete pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` pass.

## Later slices explicitly outside the first build

- Licensed adapters for FX, crypto, futures, options, indices, or commodities.
- TradingView account automation, browser scraping, paywall bypassing, or credential capture.
- Streaming sockets, unattended scheduling, automatic portfolio actions, and live-money brokers.
- Cross-provider symbol guessing, price reconciliation, synthetic conversion, and smart fallback.
- News-derived direction, confidence, readiness, sizing, entries, exits, or order instructions.

Each future network provider requires its own explicit entitlement, timestamp, calendar, adjustment,
rate-limit, failure, and instrument-binding review. Phase 28 supplies the operational boundary; it
does not certify every market or provider.
