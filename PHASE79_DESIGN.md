# Phase 79 — Progressive Account Setup

## Purpose

Reduce visual overload after setup while keeping connection fields and account discovery easy to
find whenever configuration or verification needs attention.

## In scope

- Place the existing Myfxbook form and connection-test result inside a labeled setup disclosure.
- Keep the disclosure open initially and whenever setup is incomplete, settings change, clearing
  succeeds, or a connection test fails.
- Close it only after a successful explicit connection test in the current page session.
- Make readiness actions reveal setup before focusing or initiating its existing manual action.
- Preserve all form fields, button IDs, handlers, result containers, and provider behavior.

## Non-goals and safety

- No provider request on expansion, collapse, page load, navigation, or timer.
- No automatic save, account selection, credential mutation, or persisted disclosure state.
- No prediction, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert stable disclosure content, honest open/close transitions, and retained controls.
- Tests assert disclosure handling contains no API call, timer, storage write, or synthetic success.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
