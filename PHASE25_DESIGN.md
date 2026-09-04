# Phase 25 — Restartable Offline Shadow Runtime

Base phase24-frozen: 4ede6311a72bb69f3082067fabad1610e9f3a3f1 (985 tests).
Implement a bounded, explicit-clock scheduler for precomputed canonical HeadOfDeskRequest plans.
It is an offline shadow runner, not live ingestion, strategy generation, or broker simulation.
Existing upstream provider/ingestion boundaries remain separate and frozen.

ShadowRunPlan has explicit run_id UUID4, account_id, strictly time-ordered immutable request tuple,
explicit local destination, strict deliver_alerts boolean, and nonnegative alert_ttl_us.
All request policy/proposal accounts match account_id. Plan identity is SHA-256 of full canonical
JSON; registering the same run_id with changed content fails. Registration creates PAUSED state.
No live adapter is accepted: runtime owns a RecordingAlertTransport for local-only dispatch.

start/pause/tick use explicit aware times, never a hidden clock. Tick processes at most max_jobs
due requests in schedule order. Request evaluation time is simulation time; actual journal receipt
is tick time. No recomputation of historical inputs using later data. States PAUSED/RUNNING/
COMPLETE/FAILED and checkpoint next_index persist in SQLite, with append-only transition events.

A BEGIN IMMEDIATE transaction serializes start/pause/tick across processes. Per tick, journal and
outbox use separate files and durable idempotent boundaries. Crash before runtime checkpoint commit
may replay pure composition, but decision journaling is content-idempotent and outbox claims prevent
duplicate sending. No distributed exactly-once guarantee. STARTED/UNCERTAIN/non-delivered alert
outcomes fail the run; no automatic transport retry or resume of FAILED state. Pausing stops
between bounded tick calls, not inside a committed external effect.

No user-visible background service, sleeps, implicit loop, real message sending, or credentials.
Provide a runnable CLI for manifest registration, start/pause, bounded tick, status, and audit,
with paths/times explicit. Tests cover schedule boundaries, lifecycle, content conflict, restart,
duplicate effects, replay equivalence, failures, and a 1,440-step synthetic shadow run.
That synthetic run is not sustained live-market validation or operational certification.
Live data/shadow trial, provider entitlements, and empirical performance validation remain separate
deployment work; do not claim them complete. Full and merge quality gates precede freeze.
