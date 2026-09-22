# Phase 70 — Myfxbook Daily Performance Review UI

## Purpose

Allow the operator to request and read provider-reported daily performance for an exact inclusive
date range without turning past performance into a prediction or trading input.

## In scope

- Required start/end date inputs with no automatic defaults.
- A manual **Load daily performance** action enabled only for complete Myfxbook configuration.
- Client guidance for ordered dates and the 366-date inclusive maximum; server remains authoritative.
- A neutral table of effective date and provider-reported gain percentage in returned order.
- Exact requested range, saved account ID, observation count/time, disconnected session, and
  execution `NONE` displayed with explicit “past performance is not predictive” wording.
- Honest empty, loading, current, and failed-safe states.

## Non-goals and safety

- No automatic fetch, date invention, timer, aggregation, chart projection, benchmark, or ranking.
- No prediction, recommendation, signal, setup, plan, sizing, risk input, or order action.
- No persistence or cross-use by research, desk, risk, portfolio, or execution components.
- No credentials/session tokens in the DOM, browser storage, logs, or errors.

## Verification

- Source tests assert manual-only retrieval, no default dates/timers, neutral fields, and safety copy.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
