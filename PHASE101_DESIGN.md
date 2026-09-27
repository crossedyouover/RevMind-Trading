# Phase 101 — Fail-closed hosted composition and deployment handoff

## Goal

Compose the frozen central identity verifier, tenant-scoped Trading data reader, hosted API, and
pinned-origin ASGI transport only when all trusted deployment values are explicitly supplied.

## In scope

- Five required, distinctly named configuration values separating RevMind OS identity from the
  RevMind Trading data project and frontend.
- Secret-redacted immutable configuration and a narrow application factory with injectable local
  transports for deterministic end-to-end verification.
- Deployment documentation recording the user's corrected five-product architecture.

## Non-goals

- No environment mutation, `.env` secret, remote deployment, Lovable/Supabase/DNS edit, subdomain,
  server process, or production credential.
- No change to the existing OS Website, PeptMind, Prospecting, or Emergent CRM projects.
- No writes, settings, billing/admin, broker credentials, orders, or execution routes.

## Next boundary

The code-side hosted read path is complete. Actual deployment is blocked—correctly—until the user
has the real central RevMind OS issuer/audience, a dedicated Trading Supabase project and backend
secret, and the final Trading Lovable production origin. Do not invent these values.
