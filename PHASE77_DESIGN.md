# Phase 77 — Visible Account Step States

## Purpose

Make the three-step Account Context workflow scannable by showing which step needs attention and
which steps were completed successfully in the current page session.

## In scope

- Add a compact factual state label to each existing account step.
- Derive labels only from redacted local configuration and successful current-session actions.
- Distinguish setup required, ready, in progress, completed, and failed/incomplete states.
- Reset transient completion states after settings change or clear.
- Keep the existing readiness guide as the sole primary next-action control.

## Non-goals and safety

- No automatic provider request, hidden clock, timer, or persisted completion claim.
- No account-quality score, performance interpretation, prediction, recommendation, or trade action.
- No backend, provider, research, risk, execution, or credential-storage change.
- No live-money or order authority.

## Verification

- Source tests assert the stable state labels and honest state transitions.
- Tests assert state rendering cannot call APIs, schedule work, persist state, or create a synthetic
  success state.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
