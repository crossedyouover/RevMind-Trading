# Phase 61 — Myfxbook dashboard settings API design

## Scope

Expose the frozen local Myfxbook connection-profile boundary through loopback-only dashboard API
routes. The browser may read redacted status, save a profile plus optional replacement credentials,
or clear the entire connection. This phase does not add visual controls or contact Myfxbook.

## Routes

- `GET /api/myfxbook/settings` returns only `SettingsStore.myfxbook_public()`.
- `POST /api/myfxbook/settings` accepts a strict JSON object containing schema version 1, selected
  provider account ID, IANA broker timezone, optional email/password pair, and strict clear flag.
- The existing local-session request token and loopback-origin protections are mandatory.
- Request bodies retain the existing bounded-size, JSON-content, no-cache, and generic-error rules.
- Responses never echo submitted or stored email/password values.

## Validation and behavior

- Reject unknown fields, implicit types, partial credentials, set-and-clear conflicts, invalid
  profiles, oversized bodies, malformed JSON, and non-local/unauthorized requests before mutation.
- Omitted credentials preserve existing credentials; supplied pairs replace them atomically.
- Clear removes both Myfxbook files and returns truthful `NOT_CONFIGURED` status.
- Saving returns `CONFIGURED_NOT_ACTIVE` only when both profile and credentials exist. This does not
  claim authentication or connectivity.

## Non-goals

- No account discovery request, connection test, provider login, synchronization, scheduler, UI,
  reconciliation, risk decision, alert, order, or execution authority.

## Required tests

- authenticated GET/save/update/preserve/clear lifecycle with secrets absent from every response;
- unauthorized, malformed, oversized, unknown-field, partial-secret, invalid-timezone, and
  set-and-clear rejection with no filesystem mutation;
- existing Alpaca routes/files remain unchanged;
- server source contains no provider call, adapter construction, broker import, or execution path.
