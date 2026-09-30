# Phase 104 — Bounded Official USGS Earthquake Adapter

Status: **frozen design for one narrow external global-event provider**.

## Objective

Read the official USGS `all_hour.geojson` summary feed and convert each valid feature into an
immutable `ObservedGlobalEvent` with category `NATURAL_DISASTER`.

USGS documents the GeoJSON summary feed as a programmatic interface whose features include `id`,
`place`, `time`, `updated`, `url`, `status`, and `mag`. The adapter uses only those source fields.

## Boundaries

- the origin and path are pinned to the official HTTPS USGS feed;
- redirects are disabled;
- timeout, response bytes, and feature count are bounded;
- `observed_at` is injected by the trusted caller and remains the only knowledge boundary;
- `time` becomes the source-asserted occurrence time;
- `updated` becomes source revision provenance, not RevMind knowledge time;
- countries, regions, instruments, transmission, and market impact remain unknown;
- malformed, duplicate, future-dated, oversized, redirected, or non-success responses fail closed.

## Explicit non-goals

- earthquake importance or market relevance scoring;
- geocoding source place text into countries or regions;
- World Monitor, autonomous browsing, polling, persistence, UI, alerts, or execution;
- trading signals, recommendations, portfolio changes, or real-money authority.
