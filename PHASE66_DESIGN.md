# Phase 66 — Myfxbook Current Account Summary UI

## Purpose

Make the frozen Phase 65 endpoint understandable and useful by displaying the saved account's latest
read-only summary after an explicit operator click.

## In scope

- A **Refresh saved account summary** button in the Myfxbook connection card.
- No automatic request on load, navigation, save, discovery, or timers.
- A readable card showing exact account ID, optional name, currency, balance, equity, optional
  margin/free margin, observation time, and confirmed disconnected session state.
- Honest empty, loading, current, and failed-safe states.
- Clear wording that the values are a point-in-time provider snapshot, not a signal or live stream.

## Non-goals and safety

- No positions, pending orders, transactions, performance, news, prediction, or trade plan.
- No persistence, polling, reconciliation, account switching, or implicit refresh.
- No credentials/session tokens in the DOM, browser storage, logs, or errors.
- No execution method, broker mutation, automatic order, or real-money authority.

## Verification

- Source tests assert the button calls only `/api/myfxbook/summary` after an explicit click.
- Tests assert the rendered fields, disconnected wording, and absence of timers/automatic invocation.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
