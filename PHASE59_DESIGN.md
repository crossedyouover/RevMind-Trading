# Phase 59 — Myfxbook read-only account discovery design

## Scope

Add a bounded read-only operation that lists the user's available Myfxbook accounts as immutable
canonical account snapshots. This is the provider capability a future Connections screen needs to
show an explicit account choice instead of requiring the user to type or guess a provider ID.

No credential storage, dashboard UI, account selection persistence, synchronization, reconciliation,
sentiment, or execution is included.

## Contract and mapping

- Add `list_accounts()` to the provider-neutral read-only interface.
- Call only the official `get-my-accounts` endpoint through the existing authenticated session.
- Accept at most 100 accounts; reject oversized, malformed, duplicate-ID, missing-currency, or
  non-finite balance/equity data as one atomic failure.
- Preserve provider account IDs as opaque non-empty strings and optional account names.
- Preserve exact balance, equity, and optional margin Decimals; do not calculate buying power,
  returns, exposure, or account quality.
- Assign one injected-clock UTC `observed_at` only after the complete collection validates.
- Sort deterministically by `(provider_account_id, account_name-or-empty)`.
- Return no session, credential, invitation URL, broker login, or unmodeled provider metadata.

## Safety and non-goals

- Listing an account does not select, trust, sync, reconcile, score, or authorize it.
- Demo/live provider labels are not trading permissions and are not added to the canonical model in
  this phase.
- Unknown or unavailable fields remain absent; no defaults or estimates are fabricated.
- No method can place, modify, mirror, or cancel an order.

## Required tests

- empty, singleton, reordered, 100-account, and 101-account responses;
- duplicate/blank IDs, malformed numeric/currency data, non-finite values, and atomic failure;
- exact Decimal preservation, stable ordering, one receipt boundary, and clock-after-validation;
- credential/session redaction, terminal disconnect behavior, fixed-origin security, and absence of
  execution methods or broker dependencies.
