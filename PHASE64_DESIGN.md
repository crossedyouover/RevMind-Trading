# Phase 64 — Explicit Myfxbook Account Choice

## Purpose

Make the bounded Phase 63 discovery result practical: each returned account gets a clear action that
copies its exact provider account ID into the settings form. The operator must still save separately.

## In scope

- Render discovered accounts as readable cards with account name, ID, currency, balance, and equity.
- Add one **Use this account ID** button per discovered account.
- Copy only the exact account ID returned by the authenticated bounded probe.
- Scroll/focus the settings form and explain that the choice is not saved until Save is pressed.
- Clearly mark the currently saved account when it appears in a discovery result.

## Non-goals and safety

- No automatic account selection or settings mutation after discovery.
- No credential, session-token, position, order, transaction, or performance display.
- No provider request beyond the existing explicit bounded discovery test.
- No synchronization, signal, plan, risk approval, execution, or real-money authority.
- Account IDs remain provider identity only and never imply ownership verification or trade approval.

## Verification

- Permanent source tests assert deliberate selection and separate-save language.
- Tests assert selection copies only `provider_account_id` and does not submit the form.
- Full pytest, Ruff, strict mypy, JavaScript syntax, and `git diff --check` must pass.
