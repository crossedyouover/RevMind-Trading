# Phase 18 — Deterministic Trend-Regime Evidence Foundation

Implementation contract; not frozen until the merge gate and remote tag verification succeed.

Base: `297434414ad21851493d2a3d94f33c9d9b725789`, descended from `phase17-frozen`.

## Scope and limits

Implement a first deterministic regime component for one explicitly sourced Phase 13 materialized
bar history. Trend evidence is not a broad market-wide risk-on/risk-off claim, a probability,
recommendation, or portfolio/risk decision. Volatility, cross-asset, breadth, liquidity, and
macro-regime components remain separate future designs. Do not change frozen Phases 1–17 or use
the placeholder `MarketRegime` contract to imply confidence or macro coverage.

## Request and PIT semantics

`TrendRegimeRequest` retains a complete `MaterializedBarHistory`, explicit `TrendRegimeConfig`,
and aware UTC `evaluation_at`. Config requires positive strict integer `sma_period` and
`return_period`, bounded by the frozen technical engine's maximum; neither has an implicit default.

The materialized history's `as_of` is the source-knowledge cutoff and must not exceed evaluation
time. Every supplied bar event timestamp must also be at or before evaluation time. Reject
violations instead of trimming or repairing history. Retain source identity, receipt times, UUIDs,
and all original materialization metadata. Reject repeated observation identities.

Outputs are evidence recomputed from the selected as-of history. Their bar timestamps are event
labels, not claims that this evidence was available then. A late correction can change a later
as-of recomputation; it must not mutate a prior result or be backdated to the original bar time.

## Frozen calculation composition

Call the Phase 7 technical engine once with only close SMA and arithmetic return enabled, at the
explicit requested periods. Do not reimplement those calculations, substitute feature periods,
or infer missing values. Reject missing, extra, reordered, count-mismatched, or misaligned stage
output. Phase 7 owns Decimal calculation context.

For each supplied bar retain the exact two technical feature operands and selected observation.
Availability precedence is WARMING_UP if either operand is warming, otherwise UNDEFINED if either
is undefined, otherwise AVAILABLE. Unavailable evidence has no regime label.

When both operands are available:

| Close relative to SMA | Arithmetic return | Descriptive trend regime |
|---|---|---|
| Greater | Positive | UPWARD |
| Less | Negative | DOWNWARD |
| Equal | Zero | FLAT |
| Any other combination | Any other combination | MIXED |

Comparisons use exact Decimal operands with no added threshold, rounding, score, or confidence.

## Output contract

`TrendRegimeResult` retains the complete request and an immutable tuple of `TrendRegimeSnapshot`
values, one per materialized bar in unchanged order. Each snapshot retains its full MaterializedBar,
SMA operand, return operand, availability, and optional regime. Direct construction must validate
the derived status/label, feature keys, configured periods, complete observation alignment, and
count. Nested low-level model copies must be revalidated. Empty histories produce empty results.

Structural validation does not prove external authenticity, complete historical data, or semantic
correctness of arbitrary injected technical implementations. The reference engine uses Phase 7;
injecting another engine is an explicit caller trust boundary, not an opportunity for hidden fallback.

## Non-goals and gate

No new provider, source routing, replay/storage access, scheduling, clock calls, random identity,
network, sorting/repair, resampling, staleness policy, scanner changes, insider/flow inference, LLM,
risk/portfolio authority, alert, execution, or Angelo OS integration. Future controls invoke typed
contracts without changing these rules.

Tests must cover all nine sign combinations; warming/undefined precedence; empty history; exact
evaluation boundaries; future knowledge/events; provenance and prefix stability; corrected as-of
views; strict configuration and JSON round trips; malformed/misaligned injected stages; immutable
and forged nested contracts; no fallback after errors; and forbidden dependencies. Run focused
and full suites, Ruff, strict mypy, diff checks, scope review, and merge gate before tagging/pushing.
