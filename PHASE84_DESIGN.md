# Phase 84 — Synchronized Account Guide State

## Purpose

Keep the page-level Account action accurate immediately after local account workflow state changes,
without requiring navigation away from and back to the Account page.

## In scope

- Add a local synchronization helper for the page-level Account action.
- Copy the visible Account readiness action label and delegate its click when Account is active.
- Invoke synchronization after readiness rendering changes the next action.
- Preserve all existing explicit setup, test, current-refresh, and history actions.

## Non-goals and safety

- No provider request, automatic action, timer, polling, or persisted navigation state.
- No bypass of disabled controls or configuration gates.
- No prediction, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert readiness rendering synchronizes the active Account page action.
- Tests assert synchronization is route-gated, delegates locally, and contains no API call.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
