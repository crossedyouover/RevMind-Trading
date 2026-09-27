# RevMind Trading hosted deployment handoff

This is a configuration handoff, not a deployment record. Nothing in this repository has created
or changed a Lovable project, Supabase project, DNS record, subdomain, or production secret.

## Authoritative RevMind product map

1. **RevMind OS Website** — main website built in Lovable.
2. **PeptMind App** — existing Lovable project already operating under a RevMind OS subdomain.
3. **RevMind Prospecting** — existing, nearly completed Lovable project; it will receive its own
   RevMind OS subdomain.
4. **RevMind Trading** — this repository; an independent app and Supabase data project that will
   operate under its own RevMind OS subdomain.
5. **RevMind CRM** — Emergent project; a future parallel app with its own Supabase project and
   RevMind OS subdomain.

Do not recreate, combine, rename, or overwrite those projects. Each app retains its own codebase and
Supabase data plane. RevMind OS is the future shared identity, subscription, entitlement, and app
navigation authority.

## Required backend-only configuration

| Variable | Owner | Purpose |
|---|---|---|
| `REVMIND_OS_SUPABASE_ISSUER` | RevMind OS | Exact central issuer, ending in `/auth/v1` |
| `REVMIND_OS_JWT_AUDIENCE` | RevMind OS | Exact accepted JWT audience |
| `REVMIND_TRADING_SUPABASE_URL` | Trading | Exact Trading project HTTPS origin |
| `REVMIND_TRADING_SUPABASE_SERVICE_KEY` | Trading backend only | Read Trading data through PostgREST |
| `REVMIND_TRADING_FRONTEND_ORIGIN` | Trading frontend | Exact final Lovable production origin |

The service key must never be placed in Lovable browser variables, JavaScript, local storage, source
control, logs, screenshots, or a client-side Supabase connection. Only the deployed Trading backend
receives it. The frontend sends the central RevMind OS access token to the Trading backend.

## Deployment order

1. Finish the RevMind OS identity and entitlement contract.
2. Create the separate RevMind Trading Supabase project.
3. Review and apply both ordered SQL migrations in `supabase/migrations` to that Trading project.
4. Deploy this read-only backend with all five exact variables above.
5. Create or connect the RevMind Trading Lovable frontend without altering the OS Website,
   PeptMind, or Prospecting projects.
6. Assign the Trading subdomain, then set its exact HTTPS origin in the backend configuration.
7. Verify `/v1/health`, central-token rejection/acceptance, user and organization isolation, and
   read-only research listing before connecting UI screens.

This hosted boundary does not expose broker credentials, settings mutation, billing administration,
paper-order placement, real-money execution, or news-derived trading signals.
