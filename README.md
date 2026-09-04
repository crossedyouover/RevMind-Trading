# RevMind Trading

Provider-agnostic AI-assisted market intelligence and paper-trading research platform.

> **Current status: Phase 20 — Deterministic Paper Portfolio Context. RevMind Trading DOES NOT execute trades.**

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

## Deterministic technical evidence

Phase 7 adds a batch-only, stateless technical-analysis reference engine. It produces one immutable
technical snapshot per supplied canonical `MarketBar`, using the bar's event-time timestamp. The
ten implemented feature families are close SMA, SMA-seeded close EMA, Wilder RSI, Wilder ATR,
rolling highest high, rolling lowest low, arithmetic return, rolling volume mean, population
volume standard deviation, and volume z-score.

Calculations use `Decimal` throughout under a fixed local precision of 50 with
`ROUND_HALF_EVEN`. Every feature is explicitly `WARMING_UP`, `AVAILABLE`, or `UNDEFINED`; the
engine does not fabricate partial-window values or substitute values for undefined returns or
z-scores. Results at bar N use only bars through N, so later bars cannot alter earlier evidence.

The replay/data boundary remains responsible for deciding which bars were historically knowable.
The technical engine does not access storage, replay, providers, system time, or networks. It
contains no strategy decisions, LLM intelligence, broker execution, or live TradingView inference.

## Deterministic market evidence

Phase 8 adds a batch-only, stateless interpretation layer over structurally aligned canonical
`MarketBar` and Phase 7 `TechnicalSnapshot` histories. It emits one immutable `EvidenceSnapshot`
per aligned input pair. Every snapshot preserves the complete, canonically ordered 14-key evidence
state, including `WARMING_UP`, `UNDEFINED`, and inactive outcomes rather than only active events.

Evidence rules use exact configured Phase 7 feature keys and periods. Missing inputs fail closed;
features are never substituted or recalculated. Breakout and breakdown compare the current close
with the previous snapshot's rolling extreme, preventing a current-bar tautology. Typed provenance
records the actual technical feature sources, current measurements, and configured thresholds used
by each rule.

`AlignedTechnicalHistory` guarantees structural alignment of count, canonical instrument,
timeframe, timestamp, and strict chronological order. It does not prove semantic or cryptographic
lineage between supplied bars and technical snapshots. The replay boundary remains responsible for
historical knowability; Phase 8 accepts neither `observed_at` nor `as_of`, and output at N uses only
inputs through N.

Phase 8 does not add setup composition, trading signals, recommendations, strategy logic, LLM
reasoning, risk approval, persistence, or execution.

## Deterministic setup composition

Phase 9 composes each current Phase 8 evidence snapshot into two frozen descriptive hypotheses:
`UPSIDE_BREAKOUT_ABOVE_SMA` and `DOWNSIDE_BREAKDOWN_BELOW_SMA`. Every immutable setup snapshot
contains both hypotheses in canonical order, including warming, undefined, inactive, and active
states. Composition uses only exact current-snapshot Phase 8 evidence and never scans prior or
future setup history.

An `ACTIVE` setup means only that its explicitly declared deterministic evidence conditions are
currently satisfied. It is not a signal, recommendation, prediction, or instruction to buy, sell,
enter, exit, or trade, and no predictive validity is claimed. Phase 9 has no scoring, confidence,
ranking, risk authority, portfolio authority, LLM reasoning, persistence, or execution behavior.

## Deterministic universe scanning

Phase 10 scans an explicitly supplied, single-timeframe universe of complete Phase 9
`SetupSnapshot` values. Inputs must already be unique and ordered by complete canonical instrument
identity; the scanner never sorts, repairs, deduplicates, selects a latest state, or filters the
universe. Every supplied instrument remains in the immutable output, including instruments whose
setups are warming, undefined, or inactive. Active setup keys are only an exact deterministic
projection of the complete retained Phase 9 state.

Each scan has a caller-supplied `scan_as_of` boundary, and every source setup event timestamp must
be at or before it. `scan_as_of` is not `observed_at`, ingestion time, or proof that information was
historically available. The upstream observation-time/replay pipeline remains responsible for
point-in-time eligibility.

Scanner identity ordering is not opportunity ranking. Phase 10 performs no ranking, scoring,
prediction, recommendation, signal generation, strategy decision, risk or portfolio approval, LLM
reasoning, provider access, persistence, networking, or execution.

## Read-only Alpaca market data

Phase 11 adds the first isolated real market-data adapter. Alpaca is optional and is not a core
dependency or automatic fallback. Initial support is limited to explicitly bound USD equity and
ETF identities using Alpaca's stock historical-bar and snapshot HTTP endpoints. Credentials are
optional; without both keys only this adapter is unavailable. Tests use mocked HTTP and require no
network or secrets.

The adapter returns canonical `MarketBar` and `MarketSnapshot` payloads only. Provider event time
remains payload time and never becomes `observed_at`; the existing ingestion boundary retains sole
ownership of observation identity and knowledge time. Alpaca nanosecond timestamps are explicitly
truncated, never rounded, to the frozen canonical microsecond resolution. Composite snapshot
components are not asserted to share an event time: the canonical snapshot uses only latest-trade
price and time, leaving optional daily volume and percentage change absent.

IEX and SIP feeds have different coverage and entitlement requirements. Phase 11 adds no
WebSockets, smart fallback, provider routing, data repair, freshness policy, LLM behavior, broker
connectivity, or execution. Hosted, commercial, or redistributed market-data use requires a
separate review of provider and exchange terms.

## Real-data ingestion and observation coordination

Phase 12 adds a provider-independent application boundary connecting `MarketDataProvider`
responses to the append-only observation store:

```text
Provider → canonical payload → ingestion coordinator
         → ObservedMarketData → ObservationStore → replay
```

Provider payload timestamps remain event time. Immediately after each provider call completes,
the coordinator captures one UTC `observed_at` receipt boundary shared by every payload from that
call. It then constructs all immutable observation envelopes before performing one atomic batch
write. Repeated acquisitions are retained with distinct observation identities; there is no hidden
deduplication or overwriting.

The coordinator owns neither scheduling nor streaming, transport, replay, analytics, trading, or
execution. Those responsibilities remain outside this ingestion boundary.

## Deterministic point-in-time bar materialization

Phase 13 adds a pure boundary between historical replay and deterministic technical analysis. It
accepts an explicitly supplied sequence of canonical observations already ordered by the frozen
knowledge key `(observed_at, observation_id)` and rejects future-known or noncanonical input. It
does not open replay storage, call providers, or own a clock.

Each request fixes one complete instrument identity, timeframe, source, knowledge-time `as_of`,
and optional half-open event-time range. Snapshots and nonmatching observations remain historical
facts but are excluded from that requested bar series. Sources are never blended and there is no
implicit preference or fallback.

When repeated receipts or corrections exist for the same bar event timestamp, the latest
observation in canonical knowledge order wins among facts known by `as_of`. The output preserves
the selected observation ID, receipt time, source, and optional source record ID for every bar,
and orders the selected history strictly by event time for Phase 7 consumption. No gaps are
filled, bars are not resampled or repaired, and no analytical, strategy, risk, LLM, portfolio,
alerting, or execution decision is made.

## Deterministic single-series research composition

Phase 14 adds a pure orchestration boundary for one completed Phase 13 materialized history. It
runs the frozen Phase 7 technical engine, Phase 8 market-evidence engine, and Phase 9 setup
composer exactly once in that order, using explicit deterministic configurations. The immutable
result retains the complete Phase 13 request and selected-observation provenance while requiring
one-to-one instrument, timeframe, and event-time alignment at every analytical stage.

Empty histories remain explicit and produce empty aligned stage outputs. The pipeline does not
read replay storage, call providers, own a clock, scan or rank a universe, create a signal or
recommendation, inspect a portfolio, approve risk, invoke an LLM, alert, or execute. Its explicit
request/result boundary can be invoked by a future control plane without moving domain authority
into Angelo OS or weakening deterministic risk veto supremacy.

## Deterministic multi-instrument universe coordination

Phase 15 accepts an immutable collection of complete Phase 14 results in strict canonical
instrument order. Every series must share an explicit knowledge-time cutoff and timeframe. For
each instrument it selects the latest setup snapshot whose event time is at or before the explicit
scan boundary, then invokes the frozen Phase 10 scanner exactly once.

Series with no eligible history remain present in the Phase 15 result with an explicit
`NO_ELIGIBLE_HISTORY` status; no setup is fabricated for them. The scanner projection contains
exactly the available selected setups, while the complete result retains every Phase 14 series
and its Phase 13 observation provenance. Phase 15 performs no ranking, recommendation, signal,
portfolio, risk, LLM, alert, provider, storage, control-plane, or execution work.

## Point-in-time catalyst and news facts

Phase 16 adds provider-neutral immutable catalyst facts with separate publication event time and
RevMind observation time. Historical eligibility depends only on `observed_at <= as_of`.
Materialization requires an explicit source, preserves unkeyed repeated facts, and selects the
latest knowledge-ordered version only when a source record ID explicitly identifies revisions.

Optional instrument, source-authority, and half-open publication-time filters are deterministic.
Unknown publication time is never guessed. Phase 16 adds no provider, fetching, parsing, LLM
summary, sentiment, inference, ranking, recommendation, persistence, risk, alert, or execution.

## Point-in-time insider transaction facts

Phase 17 adds an isolated `app.insiders` boundary for source-reported individual transactions.
Observations have explicit UUID4 receipt identities and UTC knowledge timestamps. Transaction
calendar dates remain dates; optional filing timestamps are separate from receipt time. Reported
quantity, price, and total value use exact nonnegative Decimals with missing values retained as
absent, without inferring direction or recalculating source assertions.

Materialization requires an immutable tuple in strict `(observed_at, observation_id)` order.
Every receipt must satisfy the requested `as_of`, including facts from excluded sources. Within
the explicitly selected source, the latest receipt for each source transaction ID wins; unkeyed
receipts remain independent. A transaction ID must identify one transaction across revisions,
not a whole filing. Instrument and half-open transaction-date/filing-time filters apply **after**
revision selection, so an old matching version cannot reappear when a correction changes a field.
Output remains in knowledge order, retaining the complete selected envelopes and stage counts.

The engine does not prove that inputs are complete or authentic, infer publisher revision order,
or implement withdrawals/tombstones. It performs no I/O, provider fetching, filing parsing, broader
flow analysis, ranking, recommendation, LLM reasoning, risk approval, alerts, or execution. Existing
frozen contracts are unchanged; future control-plane callers have no risk-veto bypass through this
boundary. See `PHASE17_DESIGN.md` for the scope and verification requirements.

## Deterministic trend-regime evidence

Phase 18 adds the first descriptive regime component for a single explicitly sourced PIT bar
history. The caller must provide close-SMA and arithmetic-return periods; no period or threshold
is selected implicitly. The frozen Phase 7 engine calculates exactly those operands once, under
its existing Decimal context. No indicators are reimplemented or silently substituted.

With both operands available, close above SMA plus positive return is `UPWARD`; close below SMA
plus negative return is `DOWNWARD`; equality plus zero return is `FLAT`; other combinations are
`MIXED`. If either operand is warming, the result is `WARMING_UP`; otherwise an undefined operand
produces `UNDEFINED`. Unavailable snapshots never receive a regime label or fabricated confidence.

The result retains the full materialized history and exact observation/feature provenance for
every bar. Both the source knowledge cutoff and bar event times must be at or before explicit
evaluation time. Historical bar labels describe an as-of recomputation, not evidence claimed to
have been available at each original event time. Late-correction views retain their later knowledge
boundary and cannot mutate prior results.

This is trend evidence, not a broad market-wide risk-on/risk-off assessment. Breadth, volatility,
liquidity, and macro components remain separate designs. Phase 18 adds no network, storage, provider,
scheduler, LLM, portfolio/risk decision, alert, control-plane integration, or execution. See
`PHASE18_DESIGN.md` for rules and limits.

## Future architecture

Phase 20 adds immutable context for one explicitly sourced paper account, restricted to equity/ETF
shares in one valuation currency. It retains account, mark, and pending-action receipt times and
rejects future-known inputs. Independent marks may be newer than account state without backdating
their knowledge. Missing marks make aggregate valuation incomplete rather than silently zero.

The pure engine derives signed market values, gross exposure, descriptive equity, and fractions
of gross exposure under a fixed Decimal context. Zero positions and zero gross exposure remain
explicit. Pending paper proposals are retained but never applied as fills or buying-power
reservations. There is no FX conversion, margin model, broker access, optimization, risk approval,
or execution. See `PHASE20_DESIGN.md` for scope and authority limits.

Phase 19 adds four pure typed desk adapters for catalyst facts, insider transactions, single-series
trend evidence, and complete setup research histories. Reports retain all upstream provenance,
configuration, ordering, and availability states. Explicit evaluation time bounds source knowledge
and bar events; reports never backdate later evidence or silently trim inputs.

`PRESENT` means records exist, not that they are actionable; `EMPTY` means only that the supplied
scoped history contains no records. Missing or invalid input fails validation. These adapters do
not recompute analytics, interpret source prose, call LLMs, rank opportunities, approve risk, or
issue orders. Broader flow/regime inference and cross-desk decisions remain deferred.
See `PHASE19_DESIGN.md` for the implemented contract and authority limits.

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

No strategy engine, backtesting engine, LLM intelligence, portfolio optimizer, execution integration, REST/WebSocket control plane, web UI, or Angelo OS integration is implemented yet.
