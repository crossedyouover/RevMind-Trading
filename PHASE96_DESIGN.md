# Phase 96 — Supabase/Lovable migration foundation

## Goal

Create a reviewed, tenant-scoped Supabase schema and RLS baseline for moving the Lovable frontend
onto hosted authentication and persistence while leaving the deterministic Python RevMind engine
outside the browser.

## In scope

- PostgreSQL tables for profiles, plans, subscriptions, promo codes, research runs, and audit events.
- Explicit role and subscription enums, ownership foreign keys, and basic validation constraints.
- RLS policies that prevent cross-user research/account access.
- A documented boundary for future RevMind API and billing/webhook integration.

## Non-goals

- No automatic execution, broker credentials, or payment-provider secrets in Supabase tables.
- No direct browser writes to subscriptions, promo codes, roles, research results, or audit events.
- No claim that this migration has been applied to a remote Supabase project.
- No automatic conversion of SQLite files or local `.revmind` secrets.

## Required next step

Connect the user's own Supabase project to Lovable, apply this migration after review, then add a
JWT-authenticated Python API adapter. The frontend must use only the publishable key; secret keys
remain in the backend or protected Edge Functions.
