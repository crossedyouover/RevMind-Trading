# Phase 85 — Account Action Busy State

## Purpose

Make bounded Account requests visibly in progress and prevent the local and page-level next actions
from appearing available while the selected manual operation is still running.

## In scope

- Track one page-session-only Account action busy state.
- Show clear testing and current-refresh progress labels in the readiness action.
- Disable both the local and synchronized page-level Account actions while busy.
- Restore the correct next action after success or failure.

## Non-goals and safety

- No new provider request, retry, timer, polling, cancellation, or persistence.
- No automatic action and no bypass of existing configuration or request bounds.
- No prediction, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert the busy labels, disabled-state synchronization, and handler lifecycle.
- Tests assert the renderer and synchronization helper contain no provider request or timer.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
