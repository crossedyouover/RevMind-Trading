# Phase 95 — Authentication, subscriptions, promo codes, and master admin

## Goal

Add a local, durable identity and entitlement boundary around the existing RevMind dashboard while
preserving every paper-trading and deterministic-risk constraint.

## In scope

- One-time first-run creation of the only `MASTER_ADMIN` account.
- Salted scrypt password hashes and opaque, expiring, revocable browser sessions.
- Authenticated access to all existing dashboard APIs.
- Master-admin creation of ordinary users, plans, promo codes, and subscription assignments.
- One-use-per-user promo redemption with bounded expiry and redemption-count rules.
- A login/setup gate and an admin workspace integrated into the existing visual system.

## Non-goals

- No public registration, password reset email, MFA, OAuth, payment processing, checkout, tax,
  invoicing, automatic renewal, or provider webhooks.
- No multi-tenant separation of existing locally stored broker credentials or research artifacts.
- No change to research, news, risk, planning, paper-order approval, or execution authority.
- No network exposure; the server remains bound to `127.0.0.1`.

## Security invariants

- Raw passwords and session tokens are never persisted.
- Bootstrap closes permanently after the first user is committed.
- Login failure does not reveal whether an email exists.
- Admin endpoints require both an authenticated session and `MASTER_ADMIN` role.
- Subscription and promo state can never authorize or modify a trade.
