# Phase 97 — RevMind OS identity boundary

## Goal

Add a fail-closed Python boundary that verifies access tokens issued by the future central RevMind
OS Supabase identity project and constructs an immutable user/organization tenant context for
RevMind Trading requests.

## In scope

- Pin the trusted issuer, audience, JWKS location, accepted asymmetric algorithms, and clock leeway.
- Verify JWT signatures and required claims before accepting an identity.
- Materialize a UUID user identity and optional UUID organization identity.
- Reject malformed bearer headers, forged/expired tokens, algorithm substitution, wrong issuer,
  wrong audience, unauthenticated roles, and unsafe configuration.

## Non-goals

- No Lovable project or remote Supabase project is created or modified.
- No route is exposed publicly and no deployment is performed.
- No service-role, broker, Myfxbook, or payment credential crosses this boundary.
- No browser is allowed to choose its tenant, role, issuer, audience, or signing key.
- No change to deterministic research, risk-veto, paper-only, news-isolation, or execution rules.

## Next boundary

Wrap a deliberately small read-only hosted API around this verifier. Every repository/storage query
must be scoped from the verified principal, never from request-body identity fields. Mutating and
paper-order endpoints remain local and unavailable to the hosted frontend until separately designed.
