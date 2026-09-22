# Phase 62 — Myfxbook Connections UI

## Purpose

Expose the frozen Phase 61 local Myfxbook settings API through a separate, plain-language card in
the Connections dashboard. This phase makes configuration understandable; it does not activate a
provider session or add trading authority.

## In scope

- A clearly labelled **Myfxbook account context (read-only)** form.
- Provider account ID and IANA broker-timezone fields.
- Optional replacement email/password fields that are cleared from the browser after every save.
- Redacted status loaded from `GET /api/myfxbook/settings`.
- Save through `POST /api/myfxbook/settings`; blank credential fields preserve stored credentials.
- An explicit, confirmed action that clears both the Myfxbook profile and credentials.
- Truthful `NOT_CONFIGURED`, `INCOMPLETE_CONFIGURATION`, and `CONFIGURED_NOT_ACTIVE` presentation.
- Accessible labels, concise instructions, and an explicit “no orders through Myfxbook” boundary.

## Non-goals

- No Myfxbook login, account discovery, synchronization, polling, reconciliation, or provider call.
- No display, echo, browser storage, or automatic population of email or password.
- No signal, prediction, order proposal, order submission, live-money authority, or risk change.
- No changes to frozen provider-neutral models, PIT rules, risk-veto supremacy, or Angelo OS contracts.

## Security and behavior rules

1. The server remains the source of truth for validation and redacted state.
2. Credentials are accepted only as a complete pair and never returned by the API.
3. Empty credential inputs mean preserve; clearing requires a distinct confirmed action.
4. The browser never writes Myfxbook secrets to local/session storage or to visible status text.
5. Configuration success must say “saved, not connected”; the UI must never imply live activity.

## Permanent verification

- Dashboard source tests assert the controls, endpoint calls, and read-only explanatory copy exist.
- Source tests reject credential persistence or rendering patterns.
- JavaScript syntax, focused dashboard tests, full pytest, Ruff, strict mypy, and `git diff --check`
  must all pass before freeze.
