# Phase 27 — Read-Only Live-Shadow Integration Design

Status: broader design with a mock-only CAPTURE_RESEARCH first slice implemented on the feature
branch; not live-deployed or frozen as a complete phase. No live access is authorized by this
document. Baseline main: `c2ecf96464309e6eec6a7cd40edb6a477c6fa132`; frozen Phase 26:
`f487a1d82caf2bb2c79dd43318dd780f05e44e24`. The recorded baseline gate is 1,003 tests,
Ruff clean and strict mypy clean. That gate is historical evidence, not a Phase 27 test result.

## Objective and scope

Design a bounded application coordinator that acquires read-only market data, persists actual
receipt history, seals an explicit PIT input bundle, runs frozen research, and records local
results. Begin with one explicitly selected source, instrument, and fixed-duration bar timeframe.
Multiple instruments, external notifications, background scheduling, live portfolio acquisition,
broker simulation, LLM interpretation, network control, and orders are outside this first slice.

The first implementation must be testable entirely offline using injected provider/clock/identity
fixtures. Real-data activation is a separate gate. A successful local test is not a live trial,
proof of source completeness, or evidence of profitable performance.

### First-slice implementation boundary

The first build target below is implemented in `app/capture` with `CAPTURE_RUN_GUIDE.md` and
`examples/capture-demo.json`. The broader contracts and acceptance criteria below remain the
target design, not a claim that all Phase 27 work is finished.

- Only CAPTURE_RESEARCH and OFFLINE_BATCH_V1 are accepted. Finite canonical input batches are
  persisted through the frozen ingestion coordinator; no live provider instance can be supplied.
- Only one-minute bars and caller-supplied ordered UTC session intervals are supported. Coverage
  is explicitly every declared interval or observed-only. No calendar interpretation is invented.
- The complete finite batch is part of immutable CycleRequest, so retries bind its content as
  well as policy. UUID4 receipt identities and injected clock values are retained in sealed inputs.
- Actual first-slice states are ACQUIRING, INPUTS_SEALED, COMPLETE, BLOCKED and UNRESOLVED.
  The durable ACQUIRING insertion includes the claim. Result and COMPLETE event commit atomically
  in capture.db; there is no decision journal in capture-only mode. Exceptions before sealing
  leave the claim unresolved on retry. After sealing, failed computation/persistence may retry
  from the same sealed inputs, never reacquire. Coverage/resource rejection is terminal BLOCKED.
- SQLite process-held locking serializes invocations across cooperating connections. A crash
  releases the lock but does not erase the durable acquisition claim. No expiring lease is used.
- Range/count/page/byte caps bound finite local work. Hard wall-time/transport-memory deadlines
  are not implemented or claimed; live transport qualification is still a separate prerequisite.
- PAPER_RESEARCH, paper-account integration, live adapters, background services and external
  control remain unimplemented. Phase 27 must not be frozen as fully complete on this slice alone.

## Frozen integration constraints

Inspection of the current repository establishes these boundaries:

- `app/data/ingestion.py`: the coordinator assigns observation UUID4s and captures one receipt
  time after each provider call, then atomically appends that call's canonical observations.
- `app/data/replay.py`: a reader pins a database snapshot and cutoff for its lifetime. A cutoff
  alone does not reproduce the same snapshot after later writes with earlier receipt times.
- Phases 13/14/18/19: materialization, research, trend and desk reports retain exact provenance;
  setup and trend must derive from the same selected history.
- `app/orchestration/models.py`: Head-of-Desk requires an explicit paper proposal and policy.
  A scanner result does not supply a proposal, paper account, risk thresholds, or reference mark.
- `app/runtime/shadow.py`: plans are immutable and fully precomputed; registration must precede
  the first evaluation. It is not an appendable live queue. Do not backdate registration to make
  newly acquired evidence fit this API, or modify the frozen runtime to accept mutable plans.
- Phase 26 grants authorize exact existing run manifests, not live acquisition or future manifest
  contents. No wildcard grant or new acquisition command may be smuggled into that schema.

Use a new application boundary for capture/research, calling frozen domain engines directly.
An archived, precomputed manifest may later support explicitly labeled offline replay; it is not
the live capture mechanism. Any future live-control schema requires a separate versioned design.

## Proposed contracts (names provisional)

All contracts are immutable, strict, versioned, revalidated at public boundaries, and canonically
serialized with content digests. Unknown fields and changed content under a reused ID fail closed.
Persist actual UUID4 identities once; never regenerate them during recovery or derive fake UUID4s.

| Contract | Required contents |
|---|---|
| CapturePolicy | Exact source and provider binding, complete instrument, timeframe, explicit UTC session intervals, lookback/event range rules, bar-closure rule, freshness bounds, resource caps, technical/evidence/trend configurations, version and digest |
| CycleRequest | UUID4 cycle ID, policy digest, explicit scheduled time, half-open acquisition range, mode CAPTURE_RESEARCH or PAPER_RESEARCH, optional complete paper input bundle |
| SealedCycleInputs | Original request/policy, actual acquisition receipt identities, ordered replay observations and digest, cutoff, snapshot extraction metadata, actual seal time, code/schema/config versions |
| CycleResult | Cycle ID, sealed-input digest, complete materialization/research/trend results, explicit availability, optional complete desk decision, actual completion time, artifact digests |
| CycleEvent | Append-only sequence, cycle ID, explicit aware event time, stage, bounded reason code and related artifact digest; no credentials or raw HTTP headers |

Do not copy the adapter's default feed into an implicit live policy. Provider/feed/source identity,
adjustment semantics, instruments, and timeframe must be explicitly bound and verified. Existing
Alpaca code is an integration candidate because it is already present, not a selected service or
claim about current plans, prices, access, or terms. Verify current official provider documentation
and the user's entitlements before activating any particular feed.

## Time, completeness, and PIT rules

1. The scheduled time is operational intent, not an observation timestamp. Inject an outer clock
   and record actual acquisition/commit/seal/completion boundaries. Never use historical bar time
   or the intended schedule as receipt time. Reject backward host-clock movement; do not clamp it.
2. Bound each bar request to explicit completed intervals using confirmed provider timestamp
   semantics and an explicit finalization delay. A timestamp inside a requested range does not
   alone prove that a bar is closed. Unsupported calendar-duration bars are out of scope initially.
3. After acquisition commits, capture `as_of`, open one fixed replay reader, and exhaust its
   pagination at that cutoff. Require receipt times <= cutoff; keep canonical knowledge order.
   Use `evaluation_at = as_of` for this research slice and actual later times for persistence.
4. Before analysis, durably seal the exact replay input tuple plus request/configuration and
   digest. Replay must consume that sealed bundle, not reopen a moving store using cutoff alone.
   Persist atomically; caps exceeded or incomplete extraction means no sealed success result.
5. Materialize through Phase 13 with explicit source and half-open event range. Latest-known
   correction selection follows the frozen algorithm. Repeated acquisitions remain distinct
   receipts. New corrections affect later cycles only, never overwrite earlier results.
6. Empty, warming and undefined analytical results retain their frozen meanings. Operational
   missing-interval or freshness failures are separately reported, never replaced by zero bars,
   forward-filled prices, an earlier favorable setup, another feed, or fake QUIET decisions.
7. Session intervals must be supplied with provenance/version, UTC boundaries and explicit policy
   for expected bars. Do not guess holidays, DST, early closes or treat every missing bar as an
   outage. The policy must distinguish documented no-trade behavior from unknown coverage.
8. Recovery after downtime captures data at its new receipt time. Historical missed slots remain
   missed; backfill is not evidence that those data were known during the outage.

## Research and risk authority

CAPTURE_RESEARCH produces descriptive analytical artifacts only, not Head-of-Desk dispositions.
It needs explicit analytical configurations but does not fabricate a paper account or proposal.

PAPER_RESEARCH additionally requires caller-supplied canonical proposal, observed paper account,
marks, pending-action state, and exact versioned risk/desk policies, all eligible at the cutoff.
Use Phase 20 portfolio context, Phase 21 risk and Phase 22 composition without changes. Build setup
and trend reports from the identical materialized history. Optional catalyst/insider data remain
absent unless explicitly supplied as eligible canonical inputs; absent is not a verified empty feed.

Missing or invalid required paper inputs blocks this mode rather than inventing share quantities,
cash, marks, thresholds, or account freshness. A valid decision with a risk veto remains QUIET.
Persist every valid disposition through Phase 24 with actual recorded_at >= evaluation_at.
No paper fill or account mutation follows a disposition. PASS_CHECKS never authorizes execution.

Initial delivery policy is DISABLED. ALERT may exist as an immutable journaled research result but
does not cause a network send. A future explicit local-recording mode may reuse Phase 23 without
changing its uncertain-outcome semantics. No real alert adapter is included in Phase 27.

## Durable lifecycle and crash semantics

Use one active writer per capture deployment, with a durable cycle-ID claim before acquisition.
Concurrent duplicate requests must not cause a second automatic acquisition for the same claim.
Use separate cycle metadata/artifact storage without changing frozen database schemas.

Lifecycle: CLAIMED -> ACQUIRING -> INPUTS_SEALED -> RESEARCH_RECORDED -> COMPLETE.
Also retain terminal BLOCKED, FAILED and UNRESOLVED states with auditable stage/reason metadata.
These are proposed Phase 27 states, not additions to Phase 25 RunState.

- Before acquisition: authorization/configuration validation must succeed and the claim commit.
- Crash during acquisition or before sealing: observation writes may have committed without a
  cycle result. Preserve them and mark the claim unresolved; never pretend acquisition was atomic
  with the cycle ledger. Do not automatically reacquire or guess the original snapshot.
- After sealing: recovery may recompute pure research from exactly sealed inputs. Verify existing
  artifact digests and use content-idempotent journal insertion before advancing the checkpoint.
- Crash after journaling: inspect the durable key and finish the same cycle without duplicating
  or rewriting the result. Original decision time remains fixed; recovery time is recorded separately.
- Duplicate COMPLETE requests return the stored result, never a fresh observation. Changed content
  under the same ID is a conflict. New acquisitions require a new cycle ID and truthful receipt time.
- Persistence failure prevents a successful response. No repair/delete/reset operation, unbounded
  retry, automatic source fallback, or distributed exactly-once guarantee is included.

## Resource limits and operation

Implement one explicit bounded invocation first; no installed scheduler or hidden perpetual loop.
Policy must bound request range, returned observations, replay pages/bytes, artifact size, acquisition
duration, and overall cycle duration. Enforce caps at the owning layer; a post-fetch limit alone
does not bound provider memory/pagination. Audit frozen adapter behavior before implementation and
raise a separate scoped design if adequate transport limits cannot be enforced without changing it.

Store secrets outside manifests, events and Git. Read-only application calls do not prove that an
API credential itself lacks trading privileges; assess host isolation and provider permissions at
activation. Configure permitted data endpoints outside user-controlled commands. No credentials
are to be inspected or requested for this design task.

Use explicit local storage roots, restricted filesystem permissions, bounded retention plans and
quiesced consistent backups across observation, cycle, journal, outbox and control databases where
present. Preserve referenced evidence; no automatic history pruning. Verify restore and disk-full
behavior before deployment. Do not label readable storage as operational readiness certification.

## Implementation acceptance tests

- Mock-only acquisition-to-sealed-research flow; optional explicit paper inputs reach the unchanged
  risk/desk/journal path, with risk veto always blocking promotion.
- Receipt time versus event/scheduled time, exact cutoff equality, future-known rejection,
  clock regression, late correction and no historical backdating after outages.
- Pagination exhaustion, fixed snapshot under concurrent writes, restart with later writes having
  earlier receipt times, and identical canonical research from the original sealed bundle.
- Half-open ranges, unclosed bars, freshness limits, empty/warming results, missing session
  intervals, DST/early-close fixtures, and no gap repair or feed fallback.
- Duplicate/conflicting cycle IDs, concurrent claim attempts, crash at every durable boundary,
  journal-before-checkpoint recovery, corrupted artifacts and persistence failures.
- Strict types, full instrument/source/policy identity, bounded resources, missing paper inputs,
  no proposal generation, no paper fills, no provider wire objects in downstream contracts.
- Dependency/side-effect checks: no broker endpoint, network notification, LLM, live credentials,
  hidden core clock, mutable Phase 25 plan, or broadened Phase 26 control grants.

Completion requires focused and full tests, Ruff, strict mypy, diff/scope review and architecture
review, followed by the normal merge/freeze lifecycle. No implementation freeze is created for
this draft. Offline acceptance and live operational acceptance must be reported separately.

## Activation decisions still required

The following are intentionally UNSET; no live launch is permitted until they are supplied:

- Data provider/feed, account entitlement and permitted usage; exact instrument identities.
- Fixed-duration timeframe, session calendar/provenance, acquisition cadence, finalization delay,
  history length, freshness/coverage criteria and all resource/time limits.
- Analytical configurations; whether paper research is wanted, and if so its explicit inputs,
  operating policy, risk limits and desk policy. Synthetic demo limits are not deployment defaults.
- Host, storage/backup ownership, secret provisioning, permitted endpoints and supervision.
- Sustained paper-only observation period, acceptance thresholds, incident/recovery procedures
  and designated reviewer. No duration, acceptable downtime or performance target is guessed.

First build target after design approval: mock-only single-cycle capture-to-research coordinator
with durable sealed inputs and adversarial recovery tests. Then qualify read-only live acquisition
under explicitly selected settings. External delivery and authenticated Angelo OS integration
remain separate designs; real-money execution remains outside the roadmap.
