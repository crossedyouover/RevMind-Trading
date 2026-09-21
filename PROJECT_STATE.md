# RevMind Trading — Project State and Continuation Contract

This file is the durable restart point for RevMind Trading. A new development session should read
this file and `README.md` before making changes.

## Current verified state

- Repository: `C:\Users\user\Documents\RevMind-Trading`
- Canonical branch: `main` (`master` is the local tracking branch)
- Frozen through: Phase 58 (explicit secure Myfxbook session disconnect)
- Frozen commit: `bb94c7d322b6c49e76a9840a1ac5648bdb08cd34`
- Frozen tag: `phase58-frozen` (peeled tag resolves to the frozen commit)
- Last frozen gate: 1,138 tests passed, Ruff clean, mypy strict clean (114 source files), `git diff --check`
  clean, tracked worktree clean
- Current capability: deterministic, point-in-time-safe flow from canonical market observations
  through technical analysis, market evidence, setup composition, and multi-instrument scanning,
  plus provider-neutral point-in-time catalyst/news and insider transaction fact materialization,
  and deterministic single-series trend-regime evidence, exposed through four pure typed
  specialist advisory evidence reports, PIT-safe single-currency paper portfolio context,
  an explicit-policy deterministic paper risk gate, and QUIET/WATCHLIST/ALERT research composition.
  Local application infrastructure now adds a durable alert outbox, append-only evaluation journal,
  explicit-clock restartable offline shadow runtime, and versioned grant-scoped control contracts
- Trading status: no broker execution, automatic trading, or real-money authority exists
- Deployment status: no live delivery adapter, continuous live-market trial, background scheduler,
  network control endpoint, or real Angelo OS integration is enabled. Local grants require a trusted
  host; they are not remote authentication. See `SHADOW_RUN_GUIDE.md` for the verified offline demo

Verify the restart point before beginning work:

```powershell
Set-Location "C:\Users\user\Documents\RevMind-Trading"
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git rev-parse "phase36-frozen^{}"
git merge-base --is-ancestor "phase36-frozen^{}" HEAD
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_continuation_tmp
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app
git diff --check
```

`HEAD` and `origin/main` must match, the peeled Phase 36 tag must resolve to
`e28ea07ae4861c2fc866daf36b41287919cebced`, and the ancestry check must exit successfully. The
continuation-contract documentation may legitimately follow the frozen implementation tag.

Phases 23–26 passed their feature-branch and merge gates with respectively 978, 985, 991, and
1,003 total tests (9, 7, 6, and 12 focused tests). Each remote main and peeled frozen tag was
verified to its exact merge SHA before advancing. The Phase 25 tests include 1,440 synthetic
minute-spaced steps with a restart; this is not evidence of sustained live-market operation.
The manifest register/start/tick/status and local control CLI examples were exercised successfully.
Test and demo databases used a writable directory outside the repository.
The existing user-level Git ignore and older pytest temp-directory permission warnings are
environmental; do not suppress untracked files and then claim they were exhaustively inspected.

## Frozen architecture constraints

These rules survive every future phase:

1. Historical eligibility is controlled only by `observed_at <= as_of`; event time never grants
   early visibility.
2. Canonical knowledge order is `(observed_at ASC, observation_id ASC)`.
3. Repeated receipts, corrections, and multi-provider facts remain distinct stored history.
4. Provider data crosses RevMind-owned interfaces and becomes canonical models before downstream
   use. No downstream component depends on provider wire formats.
5. No silent sorting, repair, deduplication, gap filling, source blending, fallback, or fabricated
   evidence is allowed unless a future explicit policy boundary is designed and approved.
6. Deterministic calculations use immutable contracts, explicit configuration, injected inputs,
   and no hidden clock, randomness, network, storage, or global mutable state.
7. Deterministic risk software has unconditional veto authority over all AI or desk output.
8. No LLM may place, modify, or cancel a trade. AI output is advisory and structurally separated
   from risk and execution authority.
9. `QUIET` remains the default future Head-of-Desk outcome.
10. Paper/shadow operation and evaluation are mandatory before real-money integration is even
    considered.
11. Angelo OS may eventually invoke explicit application contracts, but control-plane concerns
    must not own or weaken RevMind domain, PIT, risk, or execution rules.
12. A phase never pushes, merges, or freezes until its focused tests, full suite, Ruff, strict
    mypy, `git diff --check`, dirty-scope audit, and architectural review pass.

## Frozen implementation record

| Phase | Frozen capability | Frozen reference |
|---:|---|---|
| 1 | Repository architecture foundation | `dfe2e09` |
| 2 | Canonical contracts and configuration | `55fb391` |
| 3 | Provider-neutral market-data boundary | `2b631b8` |
| 4 | Observation-time ingestion foundation | `7471187` |
| 5 | Append-only historical observation store | `791a8bf` |
| 6/6.1 | Fixed-cutoff deterministic historical replay | `0edcb86` |
| 7 | Deterministic technical-analysis engine | `phase7-frozen` |
| 8 | Deterministic market-evidence engine | `phase8-frozen` |
| 9 | Deterministic setup composition | `phase9-frozen` |
| 10 | Deterministic universe scanner | `phase10-frozen` |
| 11 | Read-only Alpaca market-data adapter | `phase11-frozen` |
| 12 | Provider-independent real-data ingestion coordinator | `phase12-frozen` |
| 13 | Deterministic PIT bar materialization | `phase13-frozen` |
| 14 | Deterministic single-series research pipeline | `phase14-frozen` |
| 15 | Deterministic multi-instrument universe coordination | `phase15-frozen` |
| 16 | Point-in-time catalyst and news evidence | `phase16-frozen` |
| 17 | Point-in-time insider transaction facts (not broader market flow) | `phase17-frozen` |
| 18 | Deterministic single-series trend-regime evidence (not broad market regime) | `phase18-frozen` |
| 19 | Four pure typed specialist advisory evidence boundaries | `phase19-frozen` |
| 20 | Deterministic single-currency equity/ETF paper portfolio context | `phase20-frozen` |
| 21 | Explicit-policy deterministic paper risk gate with unconditional veto | `phase21-frozen` |
| 22 | Deterministic Head-of-Desk research disposition with risk veto supremacy | `phase22-frozen` |
| 23 | Durable provider-neutral alert outbox; local recording transport | `phase23-frozen` |
| 24 | Append-only evaluation journal and reference-price outcome metric | `phase24-frozen` |
| 25 | Restartable explicit-clock offline shadow runtime and CLI | `phase25-frozen` |
| 26 | Versioned host-granted local control contracts and CLI | `phase26-frozen` |
| 27 | Bounded Alpaca paper research, readiness, planning, explicit paper orders, and local dashboard | `phase27-frozen` |
| 28 | Provider-neutral CSV operations, durable import research, capability separation, and practical UX | `phase28-frozen` |
| 29 | Operator readiness checklist and guided workflow | `phase29-frozen` |
| 30 | Approved RevMind brand lockup asset | `phase30-frozen` |
| 31 | Official public-news fallback when Alpaca news is unavailable | `phase31-frozen` |
| 32 | Provider-neutral news endpoint and explicit source mode | `phase32-frozen` |
| 33 | Automatic first-visit market-news loading | `phase33-frozen` |
| 34 | Visible news provenance and context classification | `phase34-frozen` |
| 35 | Readable expandable market-news cards | `phase35-frozen` |
| 36 | Expanded bounded official central-bank feed coverage | `phase36-frozen` |
| 37 | Truthful feed health and partial availability | `phase37-frozen` |
| 38 | Concurrent bounded official-feed retrieval | `phase38-frozen` |
| 39 | Deterministic balanced feed selection | `phase39-frozen` |
| 40 | Friendly source names in the News Desk | `phase40-frozen` |
| 41 | Official Bank of England feed coverage | `phase41-frozen` |
| 42 | Clear automatic-versus-manual source presentation | `phase42-frozen` |
| 43 | Per-source availability health badges | `phase43-frozen` |
| 44 | Visible News Desk refresh recency | `phase44-frozen` |
| 45 | In-app plain-language workflow Help panel | `phase45-frozen` |
| 46 | Stable Help route on direct navigation and refresh | `phase46-frozen` |
| 47 | Actionable Help shortcuts to core workflow sections | `phase47-frozen` |
| 48 | Starter paper-validation setup loaded from Help | `phase48-frozen` |
| 49 | Deterministic confirmed swing pivots and break-of-structure evidence | `phase49-frozen` |
| 50 | Deterministic liquidity levels and wick-sweep evidence | `phase50-frozen` |
| 51 | Deterministic fair-value-gap lifecycle evidence | `phase51-frozen` |
| 52 | Deterministic fixed-width support/resistance zone lifecycle evidence | `phase52-frozen` |
| 53 | Bounded read-only Myfxbook account-summary adapter | `phase53-frozen` |
| 54 | Read-only Myfxbook open-position facts and IANA-time conversion | `phase54-frozen` |
| 55 | Read-only Myfxbook pending-order facts | `phase55-frozen` |
| 56 | Bounded explicitly incomplete Myfxbook recent transaction facts | `phase56-frozen` |
| 57 | Explicitly ranged Myfxbook daily performance observations | `phase57-frozen` |
| 58 | Explicit secure Myfxbook session disconnect | `phase58-frozen` |

Latest frozen merge SHAs:

- Phase 23: `403cdac14dc45b7db884f96238994bf6d95be3af`
- Phase 24: `4ede6311a72bb69f3082067fabad1610e9f3a3f1`
- Phase 25: `e24c3e99a93b46f61f006b4229df27f9ed04353e`
- Phase 26: `f487a1d82caf2bb2c79dd43318dd780f05e44e24`
- Phase 27: `02c41a377f91323f6a8a304f09d79dbbb49c05b4`
- Phase 28: `02fb7025eb5ae9846d59ea76222d5f19c706aee6`
- Phase 29: `76558b6068d1846bd48f84ef1a12d67c3a8ce850`
- Phase 30: `aa02174c40640db0ee1f12efd051cb0f9eb340d8`
- Phase 31: `6b1beed0b71e44a456e17ef49e0906e2f09e3d19`
- Phase 32: `b4c96c6cc01ef5bd7db30da9455762765b5c8f52`
- Phase 33: `f38b85644264b2a75927898aee83799e8b9ff1b1`
- Phase 34: `a9bf31dc5435b3c8e087e340f819cd3cc7a0e21a`
- Phase 35: `ac616685480e72f6ad9d5b3ae92cfda2b56def35`
- Phase 36: `e28ea07ae4861c2fc866daf36b41287919cebced`
- Phase 41: `6f5882774db887fa58dfae970ef1f0d0a64d7f64`
- Phase 42: `a9877bdffdb6507d7c91f5d12432b871c8c0610f`
- Phase 43: `ecb813f328487f1fdb812b9d40afe037666519f1`
- Phase 44: `429d25ca02ebdc21514dc6a517a82fcf376548b2`
- Phase 45: `f79e0512a60b30774b28c2ef9892bd79d5e66268`
- Phase 46: `06550538a783d1678042b6f604da3fbac2cf80a8`
- Phase 47: `6417c2efeadfc19d1a2344bc595dfb98a2983e1c`
- Phase 48: `c50b70247ac87201c66ce5632d16b6e4cde36803`
- Phase 49: `9ddccb940238e53f24a83d472dbad4e7fc727046`
- Phase 50: `8c2c6fa6bd1bf192c2edc9b722528753d1b0e226`
- Phase 51: `f583351d40923873c0c159e9a3d2acf43401493d`
- Phase 52: `7e23409dd25324c4aaf1b82b86a3bb38b63a5c07`
- Phase 53: `f5c403f4e2226e58f9aa2cf4f789d795f6e35ad6`
- Phase 54: `ac8cacfeeefa4ef5f872c8840be9fb1d81b635d9`
- Phase 55: `ad1c0b25a13d144b02abc37dd7ac44eb04e31a52`
- Phase 56: `cd5c25a31094a64d242c9468305a086f553ce258`
- Phase 57: `bee660e9097b40c0fb2131cd09f868fd5cb3847f`
- Phase 58: `bb94c7d322b6c49e76a9840a1ac5648bdb08cd34`

Phases 1–6 predate the frozen-tag convention. Their commits are immutable historical foundations
and must not be rewritten.

## Current frozen data-to-scanner flow

```text
Provider adapter
→ canonical MarketBar / MarketSnapshot
→ ingestion coordinator assigns observed_at
→ append-only ObservationStore
→ fixed-snapshot PIT replay
→ explicitly sourced PIT bar materialization
→ single-series technical/evidence/setup research
→ ordered multi-instrument universe coordination
→ deterministic scanner
```

The output is descriptive research state, not a signal, prediction, recommendation, risk approval,
portfolio action, or order.

## Agreed roadmap and implementation boundaries

Each item begins with a design audit. Phase numbers below are the continuation sequence; scope must
stay narrow enough to test and freeze independently.

### Phase 16 — Point-in-time catalyst and news evidence foundation (completed)

Define provider-neutral canonical news/catalyst observations, source authority, receipt time,
revision preservation, and deterministic PIT selection. Do not add LLM summarization, sentiment,
ranking, or provider-specific behavior to the core.

### Phase 17 — Point-in-time insider transaction facts (completed)

Implemented immutable source transaction observations with separate receipt, filing-time, and
calendar-date semantics; exact optional Decimal assertions; and revision-before-filter PIT
selection. No direction, conviction, or trade intent is inferred. See `PHASE17_DESIGN.md`.

Broader flow evidence remains deferred, not completed by insider records. Order flow, fund flows,
and ownership aggregates require distinct source contracts and a separate design before use by
specialist intelligence desks. No live insider provider, filing parser, or persistence was added.

### Phase 18 — Deterministic trend-regime evidence foundation (completed)

Implemented explicit close-SMA and arithmetic-return composition over a complete PIT bar history,
with exact provenance, evaluation-time constraints, and available/warming/undefined states.
See `PHASE18_DESIGN.md`. This is single-series trend evidence only: volatility, breadth, liquidity,
cross-asset, and macro regimes remain deferred. No broad risk-on/risk-off, LLM, or portfolio authority.

### Phase 19 — Specialist advisory evidence boundaries (completed)

Implemented typed catalyst, insider-fact, single-series trend, and complete setup-history reports.
All retain explicit evaluation time and complete provenance. PRESENT/EMPTY describe record coverage,
not actionability; malformed or missing input fails closed. Original analytical availability states
remain unchanged. No LLM interpretation, broader flow/regime inference, cross-desk composition,
risk approval, or execution authority. See `PHASE19_DESIGN.md`.

### Phase 20 — Deterministic paper portfolio-context snapshots (completed)

Implemented explicit paper-account and mark receipts, signed cash/share positions, pending paper
proposals, fixed-context Decimal valuations, gross/net exposure, descriptive equity, and fractions
of gross position exposure. Missing marks invalidate aggregate valuation rather than becoming zero.
Single-currency equities/ETFs only; pending actions are retained but not applied or reserved.
No FX, buying power, margin, broker access, risk approval, or execution. See PHASE20_DESIGN.md.

### Phase 21 — Deterministic paper risk gate (completed)

Implemented explicit-policy quantity/notional ceilings, cash floor, whole-account gross/instrument
exposure, concentration, and short-position checks, with PIT/freshness/valuation prerequisites.
Pending actions always veto this first version. Ordered reasons and complete hypothetical
projections remain auditable; failures never pass. PASS_CHECKS is not execution approval or buying
power. No default numerical thresholds, resizing, broker access, or override. See PHASE21_DESIGN.md.

### Phase 22 — Head-of-Desk research composition (completed)

Implemented QUIET defaults, exact proposal/risk/time/policy bindings, complete setup/trend history
alignment, freshness blocking, and deterministic ordered reasons. WATCHLIST requires an active
configured setup; ALERT additionally requires aligned directional trend and explicit policy opt-in.
Risk vetoes and missing risk always block promotion. Optional catalyst/insider reports remain
scoped context, not sentiment. No delivery, LLM, execution, or override. See PHASE22_DESIGN.md.

### Phase 23 — Alert delivery boundary (local infrastructure completed)

Implemented immutable ALERT-only envelopes, explicit destination authority, durable claims,
append-only attempt events, expiry, and explicit definite-failure retries. Uncertain/crashed claims
never auto-resend. The reference transport records locally; real messaging adapters remain deferred.
Alerts are not orders and external exactly-once delivery is not claimed. See `PHASE23_DESIGN.md`.

### Phase 24 — Evaluation journal and outcome measurement (bounded implementation completed)

Implemented append-only complete decision records, PIT-safe outcome receipts, digest verification,
and a versioned forward reference-price return metric. This is not fill P&L, causal counterfactual
evaluation, or evidence of profitable performance. No learning or policy mutation is enabled.
Broader empirical evaluation remains future work. See `PHASE24_DESIGN.md`.

### Phase 25 — Paper/shadow runtime (offline implementation completed; live trial deferred)

Implemented immutable canonical request manifests, bounded explicit-time ticks, durable checkpoints,
pause/start/status/audit, recovery and idempotent journal/outbox coordination. A 1,440-step synthetic
test exercises restart and scheduling. The runner does not fetch live data, simulate broker fills,
install a background service, or establish sustained live-market performance. See `PHASE25_DESIGN.md`.

### Phase 26 — Angelo OS control compatibility (local contracts completed; integration deferred)

Implemented versioned REGISTER/START/PAUSE/TICK/STATUS/HEALTH/AUDIT commands, exact principal/action/
run/manifest grants, audited denials, and durable idempotent command responses. Unresolved claims
do not re-execute. Configuration registers a new immutable plan, never overrides risk or edits a
running plan. A trusted local host supplies identity/grants; real Angelo OS transport, authenticated
identity, deployment security, and service operation remain deferred. See `PHASE26_DESIGN.md`.

Real-money execution is deliberately outside this roadmap. It requires a separate security,
regulatory, operational, and human-authorization design after successful paper/shadow evidence.

## Standard phase lifecycle

For Phase `N`, always:

1. Verify `HEAD == origin/main`, the prior peeled frozen tag is an ancestor, and the tree is clean.
2. Create `phaseN-<narrow-scope>` from current verified `HEAD`, retaining the frozen ancestry.
3. Freeze the design and non-goals before implementation.
4. Implement immutable contracts and the smallest pure/application boundary that satisfies them.
5. Add permanent adversarial tests, including forbidden-dependency and side-effect audits.
6. Run focused tests and the full quality gate using an explicit writable pytest temp directory.
7. Audit dirty scope and review severity: critical, high, medium, and low.
8. Commit only the authorized files on the feature branch.
9. Merge with an explicit merge commit into local `master`.
10. Rerun the complete gate on the merge commit.
11. Create annotated tag `phaseN-frozen` on the verified merge SHA.
12. Push `master:main` and the frozen tag.
13. Use `git ls-remote` to verify remote `main` and the peeled remote tag resolve to the same SHA.

## Exact next action

Phase 58 is frozen on `phase58-frozen`. RevMind materializes one selected Myfxbook account, open
positions, pending orders, at most 50 explicitly incomplete recent transactions, and explicitly
ranged provider-reported daily gain observations, with explicit terminal session disconnect. It has
no dashboard connection, stored credentials, reconciliation, or execution authority. Phase 59 adds
only bounded read-only account discovery. See `PHASE59_DESIGN.md`.

## Historical continuation record (superseded)

The local offline dashboard is available via `Start-RevMind.cmd` and `DASHBOARD_GUIDE.md`.
It reads the existing PowerShell demo and runs isolated synthetic captures, with chart/evidence,
history, audit and JSON export. It binds only to loopback with local-session request checks.
This separate user-facing utility does not change frozen engines or enable continuous live-market
operation, paper-account UI controls, external alerts, broker execution or Angelo OS integration.
The dashboard now exposes validated local choices for offline/Alpaca, IEX/SIP, exact watchlist
instrument identities, timeframe, session policy and optional locally stored credentials.
An explicit on-demand connection test can now request one read-only snapshot per configured identity
from the fixed Alpaca market-data origin. It maps through the frozen provider-neutral boundaries,
persists canonical observations with their actual receipt time, and exposes only redacted health
state. It does not enable streaming, scheduling, historical-bar research, account access or orders.
Dashboard/live verification: 14 focused dashboard tests, 68 combined Alpaca/dashboard tests and
1,056 total tests passed; Ruff clean and strict mypy clean (96 source files). A real bounded
IEX test returned three snapshots as `CONNECTED_READ_ONLY`; no credential values were printed.

Phase 27 first-slice implementation is on `codex/phase27-live-shadow-design`, with the broader
design in `PHASE27_DESIGN.md`. `app/capture` supplies a mock-only bounded capture-to-research
coordinator, durable sealed PIT inputs and an offline CLI; see `CAPTURE_RUN_GUIDE.md`.
This feature branch legitimately follows the canonical main baseline above. It is not a full
Phase 27 freeze. A separate PAPER_RESEARCH_V1 library now integrates explicit paper account,
proposal and policies with frozen risk/desk engines and durable journaling; see
`PAPER_RESEARCH_GUIDE.md`. Continuous live integration remains unimplemented; historical-bar
operating policies and deployment activation decisions remain unset. The on-demand dashboard probe
is not a live-shadow deployment and grants no external side-effect authority.
First-slice verification: 28 focused tests and 1,031 total tests passed; Ruff clean, strict mypy
clean (90 source files), and the offline CLI completed its three-bar example. All tests and the
CLI use synthetic inputs. The frozen/main verification block above describes the current Phase 36 baseline.
Paper-slice verification: 10 paper tests (38 combined Phase 27 tests) and 1,041 total tests passed;
Ruff clean and strict mypy clean (91 source files). All three desk dispositions and a risk-vetoed
QUIET path were exercised with synthetic inputs, including journal-before-checkpoint recovery.

The authorized Phase 23–26 offline/local implementation sequence is frozen and published.
Use `SHADOW_RUN_GUIDE.md` to run and inspect the synthetic demonstration without credentials.
Next work is a separate live-shadow deployment design: select authorized data sources and
entitlements, explicit operating/risk policies, any real alert destination, runtime host and
scheduler, and authenticated Angelo OS transport. Define backup/recovery, operational acceptance,
and a sustained paper-only observation period before enabling external effects. Do not infer
permission to obtain credentials, send live messages, deploy a network service, or place orders.
Preserve all frozen boundaries and use Phase 36 ancestry; do not reopen frozen phases implicitly.
