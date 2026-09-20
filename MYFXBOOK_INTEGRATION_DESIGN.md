# Myfxbook read-only trading context integration

## Purpose

Add Myfxbook as a provider-neutral, read-only source of trading-account facts. RevMind may use
those facts to show account health, reconcile positions and results, and calibrate risk context.
Myfxbook is not a market-data authority, signal engine, broker, or execution destination.

This integration must preserve deterministic point-in-time processing, deterministic risk
supremacy, news/sentiment isolation, explicit user approval for paper orders, and future Angelo OS
control compatibility.

## Supported information

The first implementation slice may retrieve only:

- the user's Myfxbook account list and selected account summary;
- open trades and open orders for reconciliation;
- the bounded transaction history returned by Myfxbook;
- daily balance, equity, and gain observations;
- optional community outlook as clearly labelled sentiment context.

Every displayed value must identify Myfxbook as its source and show when RevMind observed it.
Unavailable data remains unavailable; it is never estimated or silently filled.

## Explicit non-goals

- no order placement, modification, cancellation, or broker login;
- no replacement for canonical quotes, bars, exchange calendars, or instrument identity;
- no conversion of community outlook into direction, confidence, readiness, sizing, or orders;
- no automatic mirroring, copy trading, martingale behavior, or strategy promotion;
- no claim that the bounded history endpoint is a complete accounting ledger;
- no bypass of RevMind risk vetoes or approval gates.

## Provider-neutral boundary

Myfxbook-specific transport and field names terminate inside an adapter. Downstream code consumes
immutable canonical facts rather than provider JSON:

- `TradingAccountSnapshot`: provider/account identity, currency, balance, equity, margin facts,
  observed timestamp, and source provenance;
- `ExternalOpenPositionFact`: stable provider record identity, canonical instrument binding, side,
  quantity, open price/time, current provider-reported values, and observation provenance;
- `ExternalOpenOrderFact`: provider record identity, canonical instrument binding, declared order
  facts, and observation provenance;
- `ExternalTransactionFact`: provider record identity, canonical instrument binding, normalized
  event time, monetary facts, and observation provenance;
- `ExternalPerformanceObservation`: account identity, effective date, balance/equity/gain values,
  and observation provenance;
- `ExternalSentimentContext`: instrument binding, long/short population facts, observation time,
  source, and an enforced `CONTEXT_ONLY` authority label.

Canonical models must not contain credentials, session identifiers, raw URLs, provider exception
text, or mutable dictionaries.

## Point-in-time and timestamp rules

- `observed_at` is a UTC timestamp supplied by RevMind's injected clock after a complete response
  is received. Provider timestamps cannot set or move it.
- Myfxbook reports transaction times in the broker's local timezone. The user must explicitly bind
  each selected account to an IANA timezone before those records can become canonical facts.
- Missing, ambiguous, nonexistent, naive, future, or out-of-range timestamps are rejected. No local
  machine timezone or daylight-saving guess is permitted.
- Original timestamp text and declared timezone may be retained as provenance, never as authority.
- Results are ordered by canonical identity and event time using an explicit stable rule.
- A caller-provided `as_of` cutoff excludes later facts. Prefix replay cannot rewrite facts already
  observable at an earlier cutoff.

## Identity and reconciliation

- Provider account IDs are opaque strings and are never treated as broker credentials.
- Every Myfxbook symbol requires an explicit binding to RevMind's canonical instrument identity.
- Unknown or ambiguous symbols remain visible as unmapped provider facts but cannot enter exposure,
  risk, readiness, setup, or order calculations.
- Reconciliation compares external facts with RevMind records; it never mutates or closes either.
- Duplicate provider records are rejected unless byte-equivalent and explicitly deduplicated by the
  adapter's documented identity rule.
- Because Myfxbook documents a 50-transaction limit for history, the UI must label it `RECENT,
  INCOMPLETE HISTORY` and must not calculate all-time audit claims from it.

## Security and transport

- Credentials are stored only in the existing local secret store outside Git and are never returned
  by an API response, embedded in HTML, logged, or included in an exception.
- Only the fixed HTTPS origin `https://www.myfxbook.com` is allowed. Redirects, alternate origins,
  user-supplied endpoints, and TLS downgrades are rejected.
- Login creates an IP-bound provider session. The session is treated as a secret, has bounded local
  lifetime, is invalidated on authentication failure, and can be explicitly disconnected.
- Network calls use strict connect/read timeouts, response-size limits, schema validation, bounded
  pagination/call counts, and redacted error categories.
- Community-outlook retrieval has an application-side daily request budget below the documented
  provider allowance and fails closed when exhausted.

## Dashboard workflow

The Connections screen presents a separate `Myfxbook (read-only)` card:

1. enter credentials locally and connect;
2. retrieve the user's account list without exposing the session;
3. select one account and declare its broker timezone;
4. map only symbols the user wants RevMind to reconcile;
5. sync account facts on explicit action, showing last successful observation and any stale state;
6. disconnect and remove the local secret/session.

The Today screen may then show a compact `Account reality check`: balance/equity context, mapped
open exposure, recent closed-result context, reconciliation warnings, and freshness. It must say
`READ-ONLY — NO ORDERS THROUGH MYFXBOOK`.

Community outlook, if enabled, appears only in contextual evidence with the message: `Sentiment is
not a signal and cannot change the trade decision.`

## Authority and risk rules

- Myfxbook facts can reduce trust, mark data stale, expose reconciliation conflicts, or cause a
  conservative veto when configured account/exposure facts are missing or inconsistent.
- Myfxbook facts cannot increase a setup score, increase size, relax a limit, approve an order, or
  turn `WAIT` into `READY`.
- The deterministic RevMind risk engine remains supreme. Unknown exposure or stale account state
  fails closed for any workflow that declares Myfxbook reconciliation mandatory.
- Execution remains confined to explicitly supported paper-broker adapters and explicit approval.

## Required adversarial tests

- fixed-origin enforcement, redirect rejection, timeout and response-size bounds;
- credential/session redaction from values, logs, responses, exceptions, and serialization;
- session expiry, authentication rejection, disconnect, and IP-bound session replacement;
- malformed, missing, duplicate, reordered, oversized, and partially unavailable payloads;
- explicit broker-timezone conversion across daylight-saving transitions;
- rejection of ambiguous/nonexistent/future timestamps and implicit local timezone use;
- deterministic identity, ordering, serialization, replay, and `as_of` cutoff behavior;
- unknown/ambiguous symbol isolation and explicit mapping behavior;
- 50-record history limitation shown as incomplete and never promoted to an audit ledger;
- sentiment cannot alter direction, readiness, risk, sizing, plan, alert, or execution;
- account facts can only preserve or tighten risk and can never relax a veto;
- proof that the adapter exposes no order-placement method and imports no broker execution module.

## Delivery slices

1. Freeze canonical external-account fact models and read-only provider protocol.
2. Implement the bounded Myfxbook adapter with injected clock/client and exhaustive contract tests.
3. Add encrypted/local-secret settings, account selection, timezone binding, and disconnect flow.
4. Add deterministic persistence and reconciliation with freshness/conflict states.
5. Add the simple dashboard reality check and optional isolated sentiment context.

Each slice requires the full repository quality gates before merge, freeze tag, and push.
