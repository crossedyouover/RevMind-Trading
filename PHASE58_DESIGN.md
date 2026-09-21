# Phase 58 — Myfxbook explicit session disconnect design

## Scope

Add an explicit, idempotent disconnect operation to the read-only Myfxbook adapter. When a session
has been created, disconnect calls the official logout endpoint once, clears the local session even
if logout fails, and closes the owned HTTP client. This prepares the provider boundary for a later
local dashboard connection flow without adding settings, UI, persistence, or execution.

## Contract

- `disconnect()` is an explicit asynchronous lifecycle operation; `aclose()` delegates to it.
- If no login session exists, disconnect performs no logout request and only closes owned transport.
- If a session exists, call only `/api/logout.json` on the fixed official origin with that session.
- Clear the in-memory session before awaiting the network response so failures cannot leave it
  reusable.
- Never expose the session, credentials, provider message, response body, or request URL in a
  returned value or raised error.
- A successful or failed disconnect is terminal for that adapter instance. Repeated calls are
  no-ops and no operation may reconnect it.
- An injected client remains caller-owned and is not closed; the adapter itself still becomes
  closed.

## Safety and non-goals

- Disconnect does not delete credentials from a future secret store; that boundary does not exist
  yet.
- No dashboard route, account selection, scheduler, retry loop, background task, sentiment, trade
  decision, risk change, or execution method is added.
- Logout failure is reported as a redacted `MyfxbookError`, but local invalidation and terminal
  closure still occur.

## Required tests

- logout occurs exactly once after login and never before login;
- local session is cleared before a failing logout completes;
- repeated disconnect and later account calls cannot reconnect;
- owned client closes while an injected client remains open;
- redirects, authentication rejection, transport failure, and malformed logout remain redacted;
- no execution surface or broker dependency is introduced.
