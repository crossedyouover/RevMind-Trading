# Phase 82 — Configuration-Gated Connection Test

## Purpose

Prevent an avoidable failed connection attempt by enabling account discovery only after the local
redacted settings state confirms a complete profile and credential pair.

## In scope

- Render `Test and find accounts` disabled until profile and credentials are configured.
- Re-enable it after a successful settings save reports complete local configuration.
- Disable it again after successful clear or incomplete configuration.
- Preserve the readiness guide as the route to setup while testing is unavailable.
- Preserve explicit test behavior, provider calls, failure handling, and disconnect semantics.

## Non-goals and safety

- No automatic connection test, provider request, timer, or synthetic configuration state.
- No credential inspection in the browser; only existing redacted booleans may drive the control.
- No prediction, recommendation, risk approval, order control, or live-money authority.

## Verification

- Source tests assert markup defaults to disabled and redacted settings state alone gates the control.
- Tests assert gating has no API call, timer, storage write, or provider-success inference.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
