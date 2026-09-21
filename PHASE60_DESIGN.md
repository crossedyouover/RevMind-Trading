# Phase 60 — Local Myfxbook connection profile design

## Scope

Extend the dashboard-owned local settings boundary with a separate Myfxbook connection profile:
credentials, selected provider account ID, and explicit IANA broker timezone. This phase provides
storage and validation only. It does not add dashboard controls, provider calls, synchronization,
reconciliation, scheduling, or execution.

## Files and public boundary

- Store non-secret profile data in `.revmind/myfxbook.json` with schema version 1, selected opaque
  account ID, and explicit IANA timezone.
- Store email/password only in `.revmind/myfxbook.env`, separate from Git-tracked configuration,
  using the existing atomic-replacement discipline.
- Public settings expose only `profile_configured`, `credentials_configured`, selected account ID,
  timezone, and a truthful inactive status. They never return email, password, or session.
- Provide a secret accessor returning `SecretStr` values only to trusted application code.
- Clearing the connection removes both profile and credential files; partial state is reported as
  incomplete and never treated as connected.

## Validation and safety

- Require email/password together; reject blank, surrounding whitespace, CR/LF, duplicate fields,
  malformed UTF-8, oversized files, and values longer than 512 characters.
- Require a non-empty account ID of at most 128 characters without control characters.
- Validate the timezone through `zoneinfo.ZoneInfo`; never infer the machine timezone.
- Writes use exclusive temporary creation, flush, fsync, and atomic replacement. Failed validation
  changes no existing file.
- Returned dictionaries, validation messages, logs, serialization, and repr output contain no
  credential value.
- Local storage is not described as encrypted or an OS credential vault. File-access hardening and
  an OS-native secret store remain separate deployment work.

## Non-goals

- No network validation, login, account discovery call, automatic selection, background refresh,
  UI form, trading decision, risk relaxation, paper order, or real-money authority.
- Saving a profile does not mean connected, authenticated, fresh, reconciled, or safe to trade.

## Required tests

- valid save/load/public/secret-access/clear lifecycle and atomic replacement;
- every partial, blank, whitespace, control-character, duplicate, oversized, malformed, invalid
  timezone, and schema-version case fails closed;
- secrets never appear in public output or exception text;
- existing Alpaca settings and credentials remain byte-for-byte unaffected;
- no provider, network, broker, order, or execution dependency is introduced.
