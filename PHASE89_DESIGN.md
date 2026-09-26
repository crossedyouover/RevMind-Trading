# Phase 89 — Connection Failure Recovery State

## Purpose

Make a failed bounded Myfxbook connection test explicit instead of collapsing it into the generic
untested state.

## In scope

- Track connection-test state for the current page session.
- Render a clear failed state after a safe connection-test failure.
- Offer a manual retry through the existing bounded connection-test control.
- Reset the state when account settings change or are cleared.

## Non-goals and safety

- No automatic retry, provider request, timer, polling, persistence, or credential exposure.
- No prediction, score, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert failure state lifecycle, copy, and local retry delegation.
- Tests assert no request or timer is added to readiness rendering.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
