# Phase 19 — Specialist Advisory Evidence Boundaries

Status: design only; no Phase 19 implementation or frozen implementation tag.
Base: `ff8b256afadeda31015474ce1b7ef1851fb7ca49`.
Frozen ancestor: `phase18-frozen`, `78e109241a09fac9ea6eb24a86366a0eebd43330`.

## Purpose and scope

Introduce four pure, typed adapters that package existing evidence for future specialist desks.
These are evidence reports, not intelligence claims, recommendations, signals, or decisions.
Implement no LLM interpretation in this phase. Keep every frozen Phase 1–18 module unchanged.
Existing desk modules are placeholders; future work may add models and an engine under
`app/desks`, without activating the Head-of-Desk, scanner, or orchestration placeholders.

| Desk kind | Required evidence payload | Meaning and limits |
|---|---|---|
| CATALYST_FACTS | `MaterializedCatalystHistory` | Source assertions, not sentiment or verified truth |
| INSIDER_FACTS | `MaterializedInsiderHistory` | Individual transactions, not broader market flow |
| TREND_EVIDENCE | `TrendRegimeResult` | Single-series trend, not broad market regime |
| SETUP_EVIDENCE | `SingleSeriesResearchResult` | Complete descriptive setups, not trade candidates |

Do not use legacy `Signal`, `Setup`, `MarketRegime`, or `DeskDecision` as report envelopes:
their confidence, mutable collections, generated identities, or decision semantics do not fit.
Do not add a generic untyped payload dictionary or arbitrary report text.

## Proposed contracts

Use four concrete immutable request types and four corresponding report types, with a closed
literal desk-kind discriminator. Each request requires an explicit aware UTC `evaluation_at`
and exactly one required evidence payload of the type above. No optional payload, clock,
generated UUID, default evaluation time, default source, or implicit configuration.

Each report retains its complete validated request, a fixed schema version literal `1`, its
matching desk kind, and a derived `coverage` value: `PRESENT` or `EMPTY`. No configurable
authority field exists: every report is advisory by contract, including direct construction.
Reject unknown fields rather than allowing clients to smuggle action or approval attributes.

Coverage is PRESENT exactly when the retained facts (catalyst/insider), trend snapshots, or
setup snapshots are nonempty; otherwise EMPTY. PRESENT means only records exist, not that
indicators are available, facts are verified, or evidence is actionable. Retain original
warming, undefined, inactive, active, and trend states without aggregating or promoting them.
EMPTY means zero records in this supplied scoped history, not absence of events in the world.

Missing input, provider failure, malformed input, and unsupported broader evidence are not EMPTY.
Required missing input fails validation. Provider availability and retrieval are outside this
boundary. There is no silent success report after an error and no fabricated unavailable payload.

Provide an `AdvisoryDeskEngine` protocol with four explicitly typed reporting methods and one
stateless deterministic reference implementation. Each method validates and wraps exactly its
input; it calls no upstream engine. Proposed error taxonomy: `AdvisoryDeskError` with
`AdvisoryDeskInvalidInputError` for invalid canonical input. Known validation/type failures are
chained; unexpected implementation errors propagate rather than becoming empty evidence.

## PIT rules

All four source knowledge cutoffs must be at or before report evaluation time, including empty
histories. Catalyst and insider use `payload.request.as_of`; setup uses
`payload.request.history.request.as_of`; trend uses
`payload.request.history.request.as_of` and also requires its retained trend `evaluation_at`
to be at or before report evaluation time. A later report can retain earlier evidence but
must not silently recompute it or imply freshness.

Every receipt remains bounded by its retained source cutoff. Trend and setup histories must
contain no bar event later than report evaluation time. Setup inputs are rejected whole when
they contain future bars; do not trim to a latest eligible snapshot. Trend retains the stronger
Phase 18 constraint that all bars were bounded by its original evaluation time.

Catalyst publication and insider filing/date fields remain source assertions under their frozen
semantics. Do not turn these fields into knowledge time, or add a new event-date filter here.
An earlier asserted event does not admit a future receipt. A future-dated assertion already
received is not represented as a completed event or investment conclusion.

Historical output is an as-of view. Bar timestamps are event labels, not claims of availability
at those times. Corrections can change a later supplied view without mutating or backdating
earlier reports. No implicit staleness threshold: retain explicit times for later policy work.

## Provenance, ordering, and defensive validation

Retain all payload metadata, observations, source identifiers, request filters, configuration,
stage counts, technical operands, evidence, and setups. Do not flatten payloads to headline text,
active setups, or a selected latest snapshot. No ranking, joining, source blending, deduplication,
sorting, filtering, repair, revision selection, or numeric recalculation in the desk engine.

Preserve each upstream contract's order exactly. In particular, Phase 16 catalyst output uses
its frozen publication-oriented output ordering; Phase 17 insider output uses knowledge order;
bar-derived histories use event order. The canonical receipt-order rule governs receipt input,
not a newly invented common ordering for these heterogeneous materialized results.

Revalidate nested objects defensively at request and report boundaries, including low-level
`model_construct` and unchecked `model_copy` inputs. Require immutable tuples and actual typed
objects in Python collections, with equivalent canonical JSON round trips. Where frozen models
do not recursively revalidate, reconstruct their children explicitly in the new boundary.
Reject repeated receipt UUIDs within each retained history. Preserve exact Decimal serialization
and full instrument identities, not symbol-only or numeric-value-only equality.

Direct report construction must validate desk/payload correspondence and derived coverage as
strictly as the engine. Reject mutated counts, provenance, mismatched stages, future knowledge,
wrong kinds, list substitutions, nonfinite values, and incomplete nested models. Do not modify
frozen validators to accomplish this. Structural validation cannot authenticate providers,
prove history completeness, or prove arbitrary supplied analytical values were truly calculated
by reference engines. Callers retain responsibility for trusted upstream production.

## Authority and future integration

No confidence, sentiment, conviction, buy/sell direction, entry/exit price, sizing, priority,
ranking, risk approval, portfolio mutation, execution command, or alert decision is generated.
Source-supplied prose and transaction labels remain untrusted data, never instructions.

Optional future LLM interpretation needs a separate versioned contract retaining exact evidence
references and explicit inference status. It must never overwrite deterministic evidence or
acquire risk/execution authority. No model SDK, prompt, credentials, or LLM call in Phase 19.

Future deterministic risk retains unconditional veto. These reports cannot substitute for a
risk decision. Future Head-of-Desk defaults to QUIET; report presence or an ACTIVE setup cannot
authorize WATCHLIST, ALERT, or a trade. Those policies belong to their later phases.

Angelo OS may eventually invoke these typed methods through a separate application boundary.
No control-plane imports, command handlers, network, storage, scheduling, environment reads,
randomness, system clocks, or global mutable state in this implementation. No combined desk
bundle is introduced: cross-desk instrument/cutoff alignment needs its own explicit design.

## Implementation sequence and acceptance checks

1. Add the four request/report contracts and closed desk/coverage enums in `app/desks/models.py`.
2. Add the pure protocol, reference engine, and error contracts in `app/desks/engine.py`;
   export only the new public types from `app/desks/__init__.py`.
3. Add focused tests in `tests/test_advisory_desks.py`; update README capability text only
   after implementation. No changes to frozen evidence producers or dependencies.
4. Exercise all four kinds, nonempty/empty payloads, required-input errors, wrong kinds,
   direct construction, exact provenance and ordering, strict tuples, forged nested objects,
   Decimal scale, complete identity, immutability, and Python/JSON parity.
5. Test cutoff equality, offset normalization, naive/epoch/date-only time rejection, future
   knowledge even on empty input, future bars, earlier trend evaluation, corrections, and
   preservation of every warming/undefined/inactive/active state without promotion.
6. Test catalyst publication ordering separately from insider knowledge ordering; verify
   source filters and counts are retained, no input trimming, and no upstream recomputation.
7. Audit imports and calls against provider/storage/LLM/portfolio/risk/execution/control-plane
   dependencies and hidden clocks/randomness; verify no error-to-empty fallback.
8. Run focused/full pytest, Ruff, strict mypy, diff checks, and severity/scope review before
   implementation commit; rerun the gate after merge before any `phase19-frozen` tag.

## Design review outcome

The scope avoids premature LLM authority and preserves the four actual upstream contracts.
Key resolved hazards: confusing empty with unavailable; conflating trend with broad regime;
using symbol-only identity; replacing materialized order with receipt order; backdating as-of
recomputations; discarding warming/undefined states; and treating source prose as instructions.
Deferred: broader flow/regime evidence, interpretation, cross-desk composition, freshness policy,
portfolio/risk decisions, alerts, persistent audit transport, and Angelo OS integration.

This document is an implementation specification, not evidence that Phase 19 tests or code exist.
