# Phase 88 — Historical Failure Recovery State

## Purpose

Make an incomplete historical-range request unmistakable and provide a direct local recovery action.

## In scope

- Add a readiness state for `INCOMPLETE` historical loading.
- Explain that current account facts remain available while history is incomplete.
- Label the next action as retrying historical dates and route it to the existing date controls.
- Preserve explicit manual submission and the exact inclusive range contract.

## Non-goals and safety

- No automatic retry, provider request, default range, timer, polling, or persistence.
- No prediction, score, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert failure copy and reuse of the local date-selection action.
- Tests assert the readiness path contains no provider request or timer.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
