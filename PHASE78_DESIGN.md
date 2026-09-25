# Phase 78 — Progressive Refresh Controls

## Purpose

Reduce decision clutter in the current-account step by making the safe combined refresh the obvious
default while retaining specialist partial refreshes as optional advanced controls.

## In scope

- Keep `Refresh current account` visible as the primary action.
- Place `Summary only` and `Positions and orders only` inside a labeled advanced disclosure.
- Explain that partial refreshes do not produce a complete combined snapshot.
- Preserve the existing button IDs, handlers, disabled state, and result behavior.
- Disclosure interaction remains local DOM behavior only.

## Non-goals and safety

- No automatic provider request on expansion, collapse, load, navigation, or timer.
- No change to provider calls, composition rules, failure handling, account facts, or persistence.
- No prediction, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert the primary action remains visible and both partial actions remain available
  only inside the advanced disclosure.
- Tests assert the disclosure has no API call, timer, storage write, or synthetic completion state.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
