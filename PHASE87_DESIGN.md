# Phase 87 — Completed Account Workflow State

## Purpose

Make completion visible after current account facts and one exact historical range have both loaded
in the page session.

## In scope

- Add a completed readiness state when historical facts are loaded.
- Change the next action to review the already-loaded historical snapshot.
- Scroll locally to the historical snapshot without requesting or mutating data.
- Preserve the visible date controls so another explicit range can still be selected.

## Non-goals and safety

- No provider request, automatic refresh, default range, timer, polling, or persistence.
- No prediction, score, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert the completion copy, local review action, and absence of provider requests.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
