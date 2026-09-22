# Phase 71 — Dedicated Account Context Workspace

## Purpose

Reduce dashboard confusion by giving Myfxbook read-only account context its own sidebar destination,
separate from Alpaca market-data and paper-trading setup.

## In scope

- Add an **Account Context** sidebar item and stable `#account` route.
- The Set Up route shows Alpaca configuration, connection health, and its bounded probe only.
- The Account Context route shows Myfxbook configuration, discovery, summary, fact, and performance
  controls only.
- Route-specific plain-language guide text and next action.
- Correct active navigation state on click, direct navigation, refresh, and mobile layout.
- Reuse existing DOM and APIs; no provider call is triggered by navigation.

## Non-goals and safety

- No backend, contract, provider, storage, research, risk, or execution change.
- No automatic Myfxbook request, polling, credential display, or browser secret persistence.
- No signals, recommendations, trade plans, order controls, or real-money authority.

## Verification

- Source tests assert route, label, active-state mapping, focused visibility, and no navigation-triggered
  Myfxbook API request.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
