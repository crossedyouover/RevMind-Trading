# Phase 76 — Guided Account Readiness

## Purpose

Make Account Context immediately understandable by showing the current setup state, the one next
manual action, and what that action will do.

## In scope

- Add a compact readiness panel above the three account steps.
- Derive its state only from redacted local configuration state and successful actions in the
  current page session.
- Present one explicit next action: complete settings, test the connection, refresh current facts,
  or choose a historical range.
- Keep every provider request behind an existing deliberate button action.
- Reset transient readiness after configuration changes or clearing local settings.

## Non-goals and safety

- No automatic provider request on load, navigation, state rendering, or timer.
- No inferred account health, performance score, prediction, recommendation, or trade action.
- No persisted connection-test state and no claim that prior provider data remains current.
- No backend, provider, contract, research, risk, execution, or credential-storage change.
- No live-money or order authority.

## Verification

- Source tests assert the stable readiness elements and each manual next-action transition.
- Tests assert that readiness rendering contains no API call, timer, storage write, or synthetic
  success state.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
