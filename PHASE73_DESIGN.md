# Phase 73 — One-Action Current Account Refresh

## Purpose

Let the operator update the useful current Myfxbook account view with one obvious manual action
instead of having to discover and press two separate refresh buttons.

## In scope

- Add a primary **Refresh current account** button to the current-exposure step.
- On one explicit click, request the existing selected-account summary and fact-batch endpoints.
- Render each response through the existing neutral summary and facts views.
- Keep the individual summary and facts buttons available for targeted retry and diagnosis.
- Report partial failure plainly; never imply both views are current when either request fails.
- Prevent duplicate clicks while the combined refresh is in progress.

## Non-goals and safety

- No new backend endpoint, provider contract, storage, research, risk, or execution behavior.
- No automatic request on navigation, load, expansion, timer, or background schedule.
- No retries, fallback data, fabricated values, signal, prediction, recommendation, or order action.
- Both existing provider sessions must retain their bounded disconnect behavior.

## Verification

- Source tests assert the explicit button and handler, both existing API calls inside that handler,
  no timer, no order endpoint, and no request before user activation.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
