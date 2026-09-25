# Phase 86 — Historical Range Busy State

## Purpose

Keep both Account next-action surfaces honest while an explicitly selected Myfxbook historical range
is loading.

## In scope

- Reuse the page-session-only Account busy state for historical-range loading.
- Show a clear historical-loading label and disable both synchronized Account actions.
- Restore the normal date-selection action after success or safe failure.
- Preserve the exact inclusive date range and existing bounded request behavior.

## Non-goals and safety

- No new provider request, default range, retry, timer, polling, cancellation, or persistence.
- No prediction, score, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert the historical busy label and handler lifecycle.
- Tests assert the readiness renderer still contains no provider request or timer.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
