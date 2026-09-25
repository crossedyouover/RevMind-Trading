# Phase 83 — Unified Account Next Action

## Purpose

Eliminate conflicting instructions by making the page-level Account action use the same state and
manual action as the Account Context readiness guide.

## In scope

- Derive the page-level action label from the visible Account readiness action.
- Delegate the page-level click to that existing readiness control.
- Preserve setup, connection-test, combined-refresh, and historical-date behavior exactly.
- Remove the outdated shortcut to the partial summary refresh.

## Non-goals and safety

- No new provider request path, automatic action, timer, or persisted navigation state.
- No bypass of disabled controls or local configuration gates.
- No prediction, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert the page guide mirrors and delegates to Account readiness.
- Tests assert the old partial-summary shortcut is absent and no API call is added to navigation.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
