# Phase 75 — Progressive Account Detail Disclosure

## Purpose

Reduce visual overload in Account Context by keeping the factual snapshot prominent and making the
verbose provider detail sections explicitly expandable.

## In scope

- Wrap detailed account-summary facts in a clearly labeled disclosure section.
- Wrap positions, pending orders, and bounded recent transactions in a separate disclosure section.
- Keep combined snapshot and refresh controls visible without expansion.
- Automatically reveal a detail section when its targeted individual refresh completes.
- Disclosure interactions remain local DOM behavior and never request provider data.
- Preserve all existing result containers and rendering functions.

## Non-goals and safety

- No data suppression, summarization inference, ranking, health score, or recommendation.
- No automatic provider request on navigation, expansion, collapse, load, or timer.
- No backend, provider, contract, storage, research, risk, or execution change.
- No live-money or order authority.

## Verification

- Source tests assert both disclosure controls, stable result containers, targeted reveal behavior,
  and the absence of API calls or timers in disclosure interactions.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
