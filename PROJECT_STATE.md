# RevMind Trading — Project State and Continuation Contract

This file is the durable restart point for RevMind Trading. A new development session should read
this file and `README.md` before making changes.

## Current verified state

- Repository: `C:\Users\user\Documents\RevMind-Trading`
- Canonical branch: `main` (`master` is the local tracking branch)
- Frozen through: Phase 22 (deterministic research disposition, no delivery or execution)
- Frozen commit: `0460e0563e5213a91e8866152f7f1afdf73d1e76`
- Frozen tag: `phase22-frozen` (peeled tag resolves to the frozen commit)
- Last verified gate: 969 tests passed, Ruff clean, mypy strict clean, `git diff --check`
  clean, tracked worktree clean
- Current capability: deterministic, point-in-time-safe flow from canonical market observations
  through technical analysis, market evidence, setup composition, and multi-instrument scanning,
  plus provider-neutral point-in-time catalyst/news and insider transaction fact materialization,
  and deterministic single-series trend-regime evidence, exposed through four pure typed
  specialist advisory evidence reports, PIT-safe single-currency paper portfolio context,
  an explicit-policy deterministic paper risk gate, and QUIET/WATCHLIST/ALERT research composition
- Trading status: no broker execution, automatic trading, or real-money authority exists

Verify the restart point before beginning work:

```powershell
Set-Location "C:\Users\user\Documents\RevMind-Trading"
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git rev-parse "phase22-frozen^{}"
git merge-base --is-ancestor "phase22-frozen^{}" HEAD
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_continuation_tmp
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app
git diff --check
```

`HEAD` and `origin/main` must match, the peeled Phase 22 tag must resolve to
`0460e0563e5213a91e8866152f7f1afdf73d1e76`, and the ancestry check must exit successfully. The
continuation-contract documentation may legitimately follow the frozen implementation tag.

Phase 22 verification included 40 focused composition tests and the full 969-test suite on both
the feature branch and merge commit. Remote main and the peeled frozen tag were verified to the
exact merge SHA before this documentation update. Test databases used a writable directory outside
the repository.
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

## Agreed remaining roadmap

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

### Phase 23 — Alert delivery boundary

Deliver immutable approved alert records through provider-neutral adapters. Alerts are not orders.
Add idempotency and auditability without allowing delivery providers to change decisions.

### Phase 24 — Evaluation journal and outcome measurement

Persist decision-time inputs, outputs, versions, outcomes, and counterfactual evaluation without
future leakage. Learning may propose configuration changes but cannot silently mutate frozen rules.

### Phase 25 — Paper/shadow runtime

Add explicit scheduling and simulated-time/live-shadow coordination around the frozen domain
boundaries. No real broker execution. Demonstrate replay equivalence, observability, recovery,
idempotency, and sustained paper operation.

### Phase 26 — Angelo OS control compatibility

Expose versioned commands, status, health, and audit retrieval around application boundaries.
Angelo OS may start, stop, inspect, and configure authorized runs; it may not bypass PIT selection,
risk vetoes, audit persistence, or execution policy.

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

Start Phase 23 with design only, based on current verified `main` with `phase22-frozen` ancestry.
Define a provider-neutral alert delivery boundary over fully validated ALERT dispositions, with
explicit destination authority, idempotency, retry/uncertain-outcome semantics, and durable audit
requirements. QUIET/WATCHLIST must not be promoted into alerts by delivery code. Preserve risk
vetoes and exact provenance; no broker execution, LLM override, or implicit live-message sending.
