# Phase 98 — Tenant-scoped read-only hosted API facade

## Goal

Expose the smallest framework-neutral API contract needed by a future Lovable RevMind Trading
frontend while preserving the verified RevMind OS identity boundary and all trading safety rules.

## In scope

- Public `GET /v1/health` with no infrastructure or credential details.
- Authenticated `GET /v1/me` derived only from the verified signed token.
- Authenticated `GET /v1/research-runs` with a bounded limit and storage predicates derived only
  from the verified user and optional organization identity.
- A second fail-closed ownership check that refuses any cross-user or cross-organization row
  returned by a faulty storage adapter.
- Consistent no-store JSON responses and safe status codes.

## Non-goals

- No HTTP host, deployment, Lovable edit, remote Supabase mutation, or database adapter.
- No caller-supplied `user_id`, `organization_id`, issuer, audience, role, or signing key.
- No settings, secrets, imports, provider tests, broker access, paper plans, paper orders, execution,
  administrative, billing, promo-code, news-to-signal, or live-money endpoint.
- No CORS policy is invented before the final Lovable origin is known.

## Next boundary

Implement a tenant-scoped Supabase/PostgreSQL reader for the existing `research_runs` table, then
wrap this pure contract with a small deployment adapter. The deployment must pin its allowed Lovable
origin and central RevMind OS issuer through trusted environment configuration.
