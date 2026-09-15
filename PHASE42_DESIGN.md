# Phase 42 — News source clarity

## Goal

Make the News Desk state truthful at a glance by separating sources RevMind retrieves
automatically from public websites that are only outbound reading links.

## Frozen constraints

- News remains context-only and cannot affect readiness, ranking, risk, sizing, planning,
  alerts, or execution.
- Existing PIT receipt and publication timestamps, deterministic ordering, bounded fetching,
  allowlisting, and provider-neutral contracts remain unchanged.
- No paywall bypass, unrestricted scraping, browser impersonation, or silent fallback is added.
- An outbound link is never represented as an active data connection.

## Scope

- Present Federal Reserve, ECB, and Bank of England as automatic official public sources.
- Present publisher, calendar, IMF, and World Bank websites as manual reading links.
- Explain why IMF and World Bank are not automatically ingested today.
- Preserve Alpaca watchlist-news behavior when a read-only Alpaca connection is configured.

## Acceptance

- Source modes are visually distinct and use plain language.
- Every link opens the named publisher in a new tab with safe link attributes.
- Existing headline refresh, filtering, provenance, and safety copy continue to work.
- JavaScript syntax, dashboard tests, full tests, Ruff, strict mypy, and diff checks pass.

## Non-goals

- Adding an IMF or World Bank adapter without a verified stable official endpoint.
- Scraping arbitrary HTML pages.
- Adding prediction, sentiment, trade signals, or order authority from news.
