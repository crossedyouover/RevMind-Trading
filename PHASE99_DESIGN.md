# Phase 99 — RevMind OS-aligned Supabase research reader

## Goal

Read hosted research-run summaries from RevMind Trading's separate Supabase data project while
accepting tenant identity only from the verified central RevMind OS principal.

## In scope

- An additive migration that removes the incorrect dependency on this app project's `auth.users`,
  adds organization membership and research ownership, and denies browser roles direct table access.
- A backend-only, read-only PostgREST adapter with a pinned Supabase origin, bounded timeout,
  bounded response, exact user/organization filters, deterministic ordering, and strict decoding.
- A second adapter-level ownership check in addition to the Phase 98 API-level ownership check.

## Non-goals

- No remote Supabase or Lovable mutation and no credential creation or storage.
- No browser access to the service key or direct app-project tables.
- No writes, authentication UI, billing, administration, broker settings, trading, orders, news
  signals, scheduler, deployment host, or CORS policy.
- No claim that the not-yet-created RevMind OS identity or Trading Supabase projects are deployed.

## Next boundary

Compose configuration, JWT verification, this reader, and the Phase 98 facade behind a minimal HTTP
host. Pin the final Lovable origin before defining CORS, and keep every execution surface absent.
