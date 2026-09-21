# Phase 57 — Myfxbook daily performance observations design

## Scope

Extend the read-only provider boundary with an explicitly ranged daily-performance query and map
Myfxbook's official `get-daily-gain` response into immutable `ExternalPerformanceObservation`
values. The caller must provide both inclusive calendar dates; the adapter must not infer a
moving window from the machine clock.

Account synchronization, daily performance retrieval, and future persistence remain separate
operations. Dashboard UI, performance scoring, reconciliation, benchmarks, profitability claims,
sentiment, and all execution remain excluded.

## Contract and mapping

- Add `daily_performance(provider_account_id, start, end)` to the provider-neutral read-only
  interface.
- Require a non-empty account ID, `start <= end`, and a range of at most 366 inclusive dates.
- Send ISO `yyyy-mm-dd` dates to the fixed official Myfxbook origin.
- Accept the documented JSON `dailyGain` collection, including the provider's nested-list shape,
  only when it contains one flat sequence of records.
- Parse each provider `date` as `mm/dd/yyyy`; map `value` exactly to `gain_percent`. The documented
  `profit` field is deliberately not mapped because the canonical performance observation has no
  daily-profit field and silently relabeling it would be false.
- Reject dates outside the requested inclusive range, dates later than the UTC observation date,
  duplicates, malformed nesting, missing fields, non-finite numbers, and more than 366 records.
- Assign one injected-clock UTC `observed_at` only after the complete response validates.
- Sort deterministically by `effective_date`.

## Safety and truthfulness

- A provider-reported percentage is an external observation, not an independently calculated or
  audited return.
- The series is descriptive account context only. It cannot create a setup, rank an opportunity,
  predict direction, relax a limit, pass the deterministic risk gate, or authorize execution.
- No annualization, compounding, filling of missing days, currency conversion, benchmark comparison,
  or profitability conclusion is allowed.
- Any malformed record fails the entire query; no partial response is returned.

## Required tests

- inclusive date parameters, one receipt boundary, exact Decimal preservation, and deterministic
  date ordering;
- empty, singleton, nested documented response, duplicate, out-of-range, future, malformed, and
  over-limit responses;
- invalid/oversized request ranges and clock-called-only-after-validation behavior;
- provider-neutral protocol conformance, credential/session redaction, and absence of execution
  methods or broker dependencies.
