# Phase 100 — Pinned-origin read-only ASGI transport

## Goal

Make the hosted read-only contracts callable through a minimal deployment-neutral ASGI boundary
without exposing another application capability.

## In scope

- HTTP-only ASGI translation into the Phase 98 facade.
- One explicitly configured HTTPS frontend origin, exact-origin checks, and bounded preflight for
  `GET` plus `Authorization`/`Content-Type` only.
- Bounded paths, query strings and query-field count; duplicate and malformed headers fail closed.
- No-store JSON responses and no infrastructure details in transport errors.

## Non-goals

- No deployment, server dependency, environment loader, domain, DNS, TLS termination, or Lovable
  edit. The actual Lovable production origin is not guessed.
- No POST/PUT/PATCH/DELETE application route, credentials endpoint, settings, broker access,
  billing, administration, paper order, live order, or execution authority.
- No remote Supabase migration or service credential.

## Next boundary

Add a fail-closed environment composition root and deployment documentation, then exercise the
complete hosted read path against a local mock transport. Remote deployment still requires the
user's real Supabase project values and final Lovable production origin.
