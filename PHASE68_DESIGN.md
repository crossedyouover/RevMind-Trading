# Phase 68 — Myfxbook Account Facts Review UI

## Purpose

Make the frozen Phase 67 fact batch readable after an explicit operator request, so current external
account exposure can be reviewed without confusing context with a signal or granting execution.

## In scope

- A **Refresh positions and orders** button enabled only for complete saved Myfxbook configuration.
- No automatic call on load, save, discovery, summary refresh, navigation, or timers.
- Readable sections for open positions, pending orders, and recent transactions.
- Exact provider symbols and optional canonical instrument identities; unmapped symbols remain explicit.
- Visible account ID, shared observation time, disconnected session state, execution `NONE`, and
  `RECENT_INCOMPLETE` history-scope warning.
- Honest empty, loading, current, and failed-safe states.

## Non-goals and safety

- No performance series, prediction, ranking, signal, recommendation, plan, or sizing.
- No buttons for placing, modifying, closing, or cancelling anything.
- No persistence, polling, reconciliation, history, inferred mappings, or fallback account.
- No credentials or provider session token in the DOM, browser storage, logs, or errors.
- No change to deterministic risk supremacy, news isolation, PIT rules, or execution boundaries.

## Verification

- Source tests assert explicit-click-only retrieval, visible incompleteness, no timers, and no mutation
  actions or provider credential/session rendering.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
