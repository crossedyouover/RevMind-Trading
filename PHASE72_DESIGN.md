# Phase 72 — Guided Account Context Workflow

## Purpose

Make the read-only Myfxbook workspace understandable at a glance by grouping its existing controls
and results into three ordered operator steps.

## In scope

- Present three clear stages: **1. Connect account**, **2. Inspect current exposure**, and
  **3. Review past performance**.
- Keep connection testing and discovered-account selection with configuration.
- Keep summary, open positions, pending orders, and bounded recent transactions together as current
  exposure facts.
- Keep exact date-range daily performance in its own historical section.
- Add short plain-language explanations and visually consistent step containers.
- Keep every provider request behind its existing explicit button or form submission.

## Non-goals and safety

- No backend, provider, contract, storage, research, risk, or execution change.
- No polling, automatic refresh, hidden default date range, credential display, or browser secret
  persistence.
- No prediction, signal, recommendation, position sizing, trade plan, or real-money authority.
- Historical performance remains context only and never enters research or risk decisions.

## Verification

- Source tests assert the three stages, their ordering, the placement of existing controls, and the
  absence of navigation- or expansion-triggered Myfxbook requests.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
