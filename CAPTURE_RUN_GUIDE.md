# Offline capture-and-research demo

This Phase 27 first-slice example uses three synthetic one-minute bars, a caller-supplied simulated
clock and test-only analytical settings. It does not fetch live data, read credentials, evaluate a
paper proposal, send alerts, change an account or place orders. Do not use its settings as a live
operating policy. The adapter binding is explicitly OFFLINE_BATCH_V1.

From Windows PowerShell:

```powershell
Set-Location "C:\Users\user\Documents\RevMind-Trading"
.\.venv\Scripts\python.exe -m app.capture --directory .capture-demo --request examples/capture-demo.json --allow-policy 2bfebfe92eb5b76469b6da94b8f49714147cf85bc0cb12bbacaf77b66edbbeae --at 2025-02-03T14:33:10+00:00
```

Expected output contains state COMPLETE, bars 3, and mode OFFLINE_CAPTURE_RESEARCH. The sealed
digest varies between fresh directories because observation UUID4s are assigned once at acquisition;
it stays fixed on retries in the same directory. Replay is deterministic for the same sealed input
and explicit configurations, not across independently assigned observation identities.

The --allow-policy argument is explicit local host authorization for this synthetic policy digest,
not remote authentication. --at supplies simulated processing/receipt time. It must include a
timezone and cannot move backwards for new work in the same directory. The CLI does not use a
system clock, install a scheduler or accept a provider instance.

The complete request, sealed observations and research/trend result are stored in capture.db;
observations.db retains append-only receipts. capture-lock.db provides a process-held SQLite writer
lock. Repeating the exact request returns the original result without acquiring another batch.
Changed request content with the same cycle ID is rejected. New acquisitions need new cycle UUID4s.

If interrupted before INPUTS_SEALED, a retry reports UNRESOLVED and does not reacquire. Inspect the
retained receipts and audit before any new cycle; never delete or rewrite claims to force a retry.
After sealing, recovery uses that exact bundle, ignoring subsequent observation-store changes.
Result and COMPLETE audit persistence are atomic. Failed coverage or freshness checks never
produce a successful research result or a fabricated QUIET decision.

Storage is local and unencrypted. Back up all three databases as a consistent set while no capture
is running; restrict filesystem access and preserve referenced evidence. No automatic cleanup,
uncertain-claim reset, retention pruning or remote recovery service is installed.

## Library interface

`OfflineCaptureCoordinator.execute(CycleRequest)` is async and accepts only a finite canonical
bar tuple. The host supplies the storage directory, clock, UUID4 factory and allowed policy digests.
`status(cycle_id)` and `audit(cycle_id)` inspect local state/events; call `close()` when finished.
These are trusted local library calls, not grant-scoped network APIs.

The policy fixes one-minute bars, explicit ordered UTC session intervals, closure delay, freshness,
coverage mode, range/count/page/byte limits and complete analytical configurations. No session
calendar or numerical operating settings are inferred. Empty research is allowed only under the
explicit observed-only coverage policy; it does not prove the provider had no data.

## Remaining Phase 27 work

This is the mock-only capture/research first slice, not completion of PHASE27_DESIGN.md's broader
live-shadow design. PAPER_RESEARCH is rejected. Live transport resource limits and deadlines,
provider/entitlement qualification, calendar acquisition, supervised scheduling, backup/restore
operational qualification and sustained paper trials remain unimplemented. Phase 25 runtime and
Phase 26 control contracts remain unchanged. No Phase 27 implementation freeze is implied.
