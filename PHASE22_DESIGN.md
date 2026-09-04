# Phase 22 — Deterministic Head-of-Desk Composition

Status: design only; no implementation or phase22-frozen tag.
Base: `e3ac57ec42cb707b16b85fb4850e4dcbb2dd2858`.
Frozen ancestor: `phase21-frozen`, `7f602b494007e15c3c6b77bb281ff6994ec3c60c`.

## Scope and authority

Compose one exact paper proposal, one supplied risk result, and typed specialist evidence into
QUIET, WATCHLIST, or ALERT. These are research-disposition records, not signals to execute,
delivery instructions, account changes, or claims of predictive validity.
QUIET is the default when evidence, alignment, policy support, or passing risk is unavailable.
A risk veto or missing risk result always prevents both WATCHLIST and ALERT.
No confidence, LLM interpretation, sentiment, ranking, implicit thresholds, or override.

This is a first narrow setup/trend composition, not a complete discretionary investment desk.
Catalyst and insider reports may be retained as optional context but cannot promote or demote a
disposition based on unmodeled sentiment or conviction. Broader flow and market regime remain
deferred. Do not reinterpret source prose as instructions.

## Explicit contracts

- `HeadOfDeskPolicy`: policy_id, policy_version, account_id, expected_risk_policy_id,
  expected_risk_policy_version, required `setup_key: SetupKey`, required strict booleans
  enable_watchlist and enable_alert, and required strict nonnegative integer max_bar_age_us.
  No default numerical thresholds or enabled escalation.
  Require enable_alert implies enable_watchlist.
- `HeadOfDeskRequest`: explicit `proposal: PaperRiskProposal`; required-but-nullable
  `risk: PaperRiskResult | None`, `setup: SetupDeskReport | None`,
  `trend: TrendDeskReport | None`, `catalyst: CatalystDeskReport | None`,
  `insider: InsiderDeskReport | None`; complete policy; explicit aware as_of and evaluation_at,
  with as_of <= evaluation_at. All fields must be supplied, including explicit nulls.
- `HeadOfDeskResult`: full request, strict integer schema_version=1, derived disposition,
  immutable ordered reason-code tuple, and optional selected setup/trend snapshots retaining
  their exact source state. Selected snapshots are not standalone substitutes for full reports.

Use immutable extra-forbidding models, actual typed Python nested objects/tuples, strict aware
Python times/ISO JSON strings, and canonical JSON round trips. Defensively revalidate nested
and existing instances, including unchecked copies. Do not alter any frozen upstream validator.
Do not reuse legacy DeskDecision with its generated IDs and confidence/status semantics.

Missing optional evidence is represented only by explicit None, never a fabricated empty report.
Malformed supplied evidence is a validation error, not absence. An upstream error must be handled
by the calling application as unavailable/non-promoting; this pure boundary neither catches an
upstream engine call nor accepts arbitrary exception objects.

## Binding and PIT eligibility

Risk request proposal must match request proposal in complete canonical serialization, including
UUID, account, instrument, signed quantity, reference mark, source, receipt times, and Decimal
scale. Matching proposal_id alone is insufficient. Proposal account, composition policy account,
risk proposal/context/policy accounts must all match; validate expected risk policy ID/version.
The full actual risk policy remains retained, not reconstructed from its version label.

All retained source cutoffs must be <= composition as_of. All report evaluation times, the
trend engine's evaluation time, risk evaluation time, and risk portfolio evaluation time must
be <= composition evaluation_at. Proposal/mark receipt times must be <= composition as_of.
Proposal and mark event times must be <= evaluation_at. Existing nested contracts retain their
own stricter relationships. Reject promotion rather than trimming future observations.

Require risk.request.evaluation_at == composition evaluation_at: a historical PASS_CHECKS is
not reusable at a later time without an explicit new risk evaluation. Also require
risk.request.as_of == composition as_of. Preserve prior risk outcomes rather than recalculating
them within this composer. Exact risk cutoff/evaluation equality provides the reuse rule, while
Phase 21's retained policy controls account, mark, and proposal freshness. HeadOfDeskPolicy
contains only max_bar_age_us as a freshness threshold; no separate risk-age allowance exists.

Every supplied setup/trend materialization must identify the proposal's full instrument, not just
symbol. Setup and trend, when both present, must retain identical complete MaterializedBarHistory
serialization: source, cutoff, event filters, selected receipt identities, marks, and stage counts.
Different analytics configuration is allowed; the common underlying bar history is not negotiable.
Their materialization cutoff must equal composition as_of. Do not blend providers or corrections.

Optional catalyst/insider histories must have explicit request.instrument equal to proposal
instrument and request.as_of equal to composition as_of. Broad or differently scoped reports are
not silently filtered. Every supplied report must pass compatibility checks even when its content
would not promote a disposition; malformed or incompatible optional context is not ignored.
Their output ordering stays frozen: catalyst publication-oriented, insider knowledge order.

The proposal reference mark is separate valuation evidence, not asserted to be the final bar
close. Do not conflate mark and bar receipt IDs or silently replace one with the other.

## Selection, freshness, and deterministic disposition

From a compatible nonempty setup report select exactly the final setup snapshot in its already
strict event order. Do not search backwards for a more favorable ACTIVE setup. Its timestamp must
be <= evaluation_at and its age must be <= max_bar_age_us; calculate exact integer microseconds.
Select the configured setup_key from the complete retained frozen catalogue, never a substitute.
Preserve WARMING_UP, UNDEFINED, INACTIVE, and ACTIVE states.

From a compatible nonempty trend report select exactly its final snapshot, retaining observation
and operands. When both selected snapshots exist their event timestamps and timeframe must match.
Trend age follows the same max_bar_age_us bound. Empty trend is not a FLAT or MIXED observation.
An unavailable trend snapshot remains explicitly warming/undefined, not inferred neutral.

The following rule applies only after every binding/PIT/risk eligibility check passes:

| Evidence and explicit policy | Disposition |
|---|---|
| WATCHLIST disabled | QUIET |
| No setup report, empty setup history, or configured setup not ACTIVE | QUIET |
| ACTIVE setup but absent/empty/warming/undefined/mixed/flat/opposing trend | WATCHLIST |
| ACTIVE setup and matching directional trend, ALERT disabled | WATCHLIST |
| ACTIVE setup and matching directional trend, ALERT enabled | ALERT |

Required direction correspondence is exact: UPSIDE_BREAKOUT_ABOVE_SMA pairs with UPWARD;
DOWNSIDE_BREAKDOWN_BELOW_SMA pairs with DOWNWARD. Additionally the proposal quantity_change
must be positive for the upside key or negative for the downside key; a mismatch yields QUIET.
This correspondence checks compatibility of caller-supplied hypotheses, not a new trade signal.

Stale or misaligned supplied evidence yields QUIET, not WATCHLIST. In particular a stale trend
cannot be treated as absent to obtain WATCHLIST. Compatible unavailable trend may support only
WATCHLIST; it can never support ALERT. Absence of catalyst or insider context does not block or
promote the first version's explicit setup/trend rule.

## Reason codes and precedence

First evaluate independent eligibility blockers in this fixed order, emitting each once:
RISK_UNAVAILABLE, RISK_VETO, PROPOSAL_MISMATCH, ACCOUNT_MISMATCH, RISK_POLICY_MISMATCH,
RISK_BOUNDARY_MISMATCH, FUTURE_KNOWLEDGE, FUTURE_EVALUATION, INSTRUMENT_MISMATCH,
EVIDENCE_SCOPE_MISMATCH, BAR_HISTORY_MISMATCH, STALE_SETUP, STALE_TREND,
SNAPSHOT_MISMATCH, PROPOSAL_DIRECTION_MISMATCH.
Skip checks requiring absent inputs rather than inventing measurements. Missing risk produces
RISK_UNAVAILABLE; it cannot also produce RISK_VETO. If any blocker occurs, disposition is QUIET,
selected output snapshots are None, and no disposition-support reasons are appended.

With no blocker, retain available final selected snapshots and emit exactly one reason:
WATCHLIST_DISABLED, SETUP_UNAVAILABLE, SETUP_NOT_ACTIVE, TREND_NOT_SUPPORTING,
ALERT_DISABLED, or SETUP_AND_TREND_SUPPORTED, following table order.
SETUP_UNAVAILABLE covers absent or empty setup history. SETUP_NOT_ACTIVE covers all three
non-active states, with the actual retained snapshot distinguishing them.
TREND_NOT_SUPPORTING covers absent/empty/non-directional/opposing/unavailable compatible trend.
The final support reason accompanies ALERT; no free-text justification is generated.

Direct result construction must reproduce disposition, reason order, and selected snapshots
using shared pure helpers. Reject forged promotions, omitted blockers, duplicate reasons, partial
evidence, changed provenance, stale passes, and added approval/override/confidence fields.
The complete request remains retained even when selected output snapshots are None.

## Implementation placement and non-goals

Add app/orchestration/models.py for contracts and pure composition helpers; implement the existing
app/orchestration/desk.py placeholder as DeterministicHeadOfDeskEngine.compose(request), conforming
to HeadOfDeskEngine. Export public types in app/orchestration/__init__.py. Leave app/desks/head.py
and all Phase 1–21 implemented modules unchanged.

Use HeadOfDeskError with chained HeadOfDeskInvalidInputError for malformed input and
HeadOfDeskComputationError for arithmetic failures encountered while revalidating frozen inputs.
Unexpected errors propagate and are non-promoting. No fallback, data fetching, risk recomputation,
analytics invocation, LLM, persistence, network, generated IDs, clocks, scheduling, ranking,
delivery, portfolio mutation, or broker/paper execution.

Future Angelo OS may call this typed contract but cannot bypass its checks. Phase 23 may design
delivery of a validated ALERT record; this phase does not send anything. Human or model judgments
cannot mutate an existing disposition or risk result. A changed proposal/policy/evidence set
requires a new deterministic evaluation and an independently retained result.

## Acceptance gate

Test the complete disposition table, both setup directions and proposal signs, every blocker,
multi-blocker ordering, missing/veto/passing risk, exact proposal identity versus UUID-only matches,
policy/account binding, same-time risk reuse, strict knowledge boundaries, microsecond freshness,
full instrument/source/history alignment, differing corrections, malformed optional evidence,
empty histories, unavailable analytical states, and no search back to earlier ACTIVE snapshots.

Test Python/JSON parity, strict types, exact Decimal/provenance preservation, forged nested copies,
direct-result promotions, immutability, no override field, upstream arithmetic errors, and forbidden
dependencies/calls. No LLM or real providers in tests. Run focused/full tests, Ruff, strict mypy,
diff checks, and severity/scope audit; repeat after merge before tagging, pushing, verifying, and
updating PROJECT_STATE. A design document is not evidence that implementation or tests exist.
