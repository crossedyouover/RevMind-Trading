# Phase 26 — Local Angelo-Compatible Control Contracts

Base phase25-frozen: e24c3e99a93b46f61f006b4229df27f9ed04353e (991 tests).
Implement versioned commands/responses around the offline runtime, not an Angelo OS connection,
network server, identity provider, or production authentication system.

Host-owned grants bind principal_id, run_id, exact manifest digest, and allowed action tuple.
Commands include explicit UUID4 command_id, principal/run/digest, action, aware issued_at,
strict max_jobs, and required-nullable plan. REGISTER requires a matching plan; all other commands
forbid plan. Actions REGISTER/START/PAUSE/TICK/STATUS/HEALTH/AUDIT only; there is no arbitrary path,
shell, policy edit, risk override, provider, or execution command. Configuration means registering
a new immutable plan, not mutating a running plan. The runtime remains local-recording-only.

SQLite command ledger records canonical command and STARTED before dispatch. Repeating an exact
completed command returns the saved response; changed content under the same command_id fails.
STARTED/uncertain command replay never repeats the effect. Check current grant before returning a
saved response; revoked authority cannot retrieve earlier command results through replay.
Persist command response digest and verify it on retrieval. Runtime checkpoints/outbox/journal
remain the authority on effects. No distributed exactly-once claim.

Response outcomes COMPLETED/REJECTED/UNCERTAIN with typed runtime status, optional audit rows,
and constrained reason strings. Malformed input fails validation. Unauthorized command is rejected
without reading the target runtime; a plan digest mismatch is rejected before any mutation.
Expected runtime validation failures become REJECTED; unexpected failures are UNCERTAIN, never
success. A crash after command claim requires inspection; no automatic claim reset or retry.

HEALTH means manifest/journal/outbox are readable and reports FAILED run state if present; it is
not a trading safety or availability certification. AUDIT reads explicit run-local runtime events.
Caller identity is supplied by a trusted embedding host. An eventual IPC/HTTP adapter must
authenticate identity and protect grant configuration; untrusted clients must not choose grants.
No remote Angelo service is contacted and no live authority is enabled.

Tests cover authorization, action and digest scope, immutable configuration, version/types,
idempotency/conflicts, revoked grants, unresolved claims, exceptions, health/audit, and full
register/start/tick/status flow. Provide local JSON CLI and usage documentation.
Full and merge gates precede freeze. Live integration/security review remains deployment work.
