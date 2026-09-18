# Phase 48 — Sustained paper-validation program

## Objective

Measure whether RevMind behaves reliably over a sustained paper-only observation period before
any live-money consideration. This phase evaluates the frozen system; it does not change trading
authority or enable automatic orders.

## Required evidence

- Fixed, versioned universe and exact provider identities.
- Fixed timeframe, session policy, risk policy, and observation schedule.
- Every cycle records provider receipt time, completed-bar cutoff, source health, readiness result,
  risk disposition, paper-plan result, and any explicitly approved paper order.
- Restart/recovery test with no duplicate cycle, journal, or alert effects.
- Daily reconciliation of observations, paper account state, and local journal.
- Out-of-sample outcome measurement with costs and missing-data flags preserved.

## Minimum acceptance gates

- No live-money endpoint or automatic order path is reachable.
- No cycle uses a bar newer than its observation receipt boundary.
- Every risk veto remains authoritative and auditable.
- No unexplained data gaps, duplicate receipts, or stale-data promotions.
- Paper outcomes are reported descriptively; no profitability claim is made from a short sample.
- Operator can pause, resume, inspect, and export the complete evidence trail.

## Non-goals

- No real-money execution.
- No LLM-generated trade authority or news-derived signals.
- No martingale, leverage expansion, or automatic parameter mutation.
- No claim that paper performance predicts live performance.

## Exit decision

At the end of the observation window, produce a signed evaluation report listing sample size,
coverage, failures, risk vetoes, paper outcomes, unresolved limitations, and an explicit decision:
continue paper validation, revise the research policy, or stop. A live-trading proposal requires a
separate security, compliance, and human-authorization design after this report.
