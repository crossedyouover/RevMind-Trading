# Phase 29 — Operator Readiness and Guided Workflow

Status: approved for implementation from frozen Phase 28 SHA
`02fb7025eb5ae9846d59ea76222d5f19c706aee6`.

## Objective

Make the local dashboard answer, in plain language, what the user can safely do next. Add a
deterministic presentation-only readiness checklist for market-data setup, read-only verification,
current analysis, and paper-plan eligibility.

## Inherited constraints

- The checklist observes existing application state; it creates no analytical evidence.
- News, UI state, and explanatory copy cannot promote readiness, size a position, or authorize an
  order.
- Stale or absent completed bars remain blocking conditions.
- Only a server-authorized READY assessment may create a risk plan.
- Deterministic risk retains unconditional veto authority.
- Paper submission still requires the existing explicit one-time human confirmation.
- Live-money hosts, automatic execution, provider guessing, and unattended scheduling remain absent.

## First slice

Add a Home readiness card with four ordered states:

1. Market data configured.
2. Read-only connection verified, or local CSV lane selected.
3. A current analysis has completed.
4. A server-authorized paper plan is eligible for human review.

Each row is `DONE`, `NEXT`, or `LOCKED`, derived only from already-returned settings, health, and
research response fields. The card exposes one contextual action and explains that locked steps
cannot be skipped. Restored research is visibly historical and never satisfies current-plan
eligibility.

## Acceptance

- First launch points to Set Up or Upload Prices.
- Saved but unverified Alpaca settings point to the read-only connection test.
- Verified settings point to Check Prices.
- WAIT and CAUTION results never show paper-plan eligibility.
- READY without a successful server plan remains locked.
- The checklist updates after settings, health checks, scans, imports, and plan calculation.
- Focused tests, full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` pass.

