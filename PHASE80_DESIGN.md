# Phase 80 — Compact Historical Performance Review

## Purpose

Keep exact historical-range provenance immediately visible without letting a long daily observation
table dominate the Account Context page.

## In scope

- Add a compact factual summary for the explicitly requested inclusive range and observation count.
- Place provider-reported daily observation rows behind a labeled local disclosure.
- Keep empty and failure states explicit; never invent or interpolate observations.
- Preserve the exact date inputs, request payload, table columns, and provider response content.
- Opening or closing daily rows remains local DOM behavior only.

## Non-goals and safety

- No gain aggregation, return calculation, ranking, trend interpretation, or prediction.
- No automatic date range, provider request, timer, persisted disclosure state, or hidden clock.
- No research, risk, recommendation, order-control, or live-money authority.

## Verification

- Source tests assert the visible exact-range summary, daily-row disclosure, and honest empty/failure
  behavior.
- Tests assert disclosure handling has no API call, timer, storage write, or inferred metric.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
