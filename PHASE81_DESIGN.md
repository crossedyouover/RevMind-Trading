# Phase 81 — Account Result Invalidation

## Purpose

Prevent point-in-time facts from a previous account configuration from remaining visually available
as though they belonged to the newly saved or cleared account context.

## In scope

- After a successful settings save or clear, replace connection-test, combined snapshot, detailed
  summary, exposure, and historical-performance results with explicit not-loaded messages.
- Collapse verbose result disclosures after invalidation while leaving setup visible for retesting.
- Reset all current-session verification and completion state consistently.
- Invalidation remains local DOM behavior and performs no provider request.

## Non-goals and safety

- No request cancellation, provider call, automatic refresh, timer, or persisted UI state.
- No deletion of durable provider observations, audit evidence, settings beyond explicit clear, or
  historical records.
- No prediction, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert every account result surface is invalidated after successful save and clear.
- Tests assert invalidation contains no API call, timer, storage write, or synthetic fresh state.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
