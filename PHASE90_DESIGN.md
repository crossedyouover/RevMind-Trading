# Phase 90 — Local Paper Research Release

## Purpose

Close the open-ended development sequence with a clearly defined, supportable v1.0 release. The
release is a local market-research and explicitly approved paper-trading application; it is not an
autonomous trader and it does not support real-money execution.

## In scope

- Publish one concise release-status document that says what works, how to start, and what remains
  intentionally unavailable.
- Make the release status discoverable from the README and dashboard guide.
- Set the package version to 1.0.0.
- Refresh the durable project state through Phase 90.
- Verify the launcher/CLI surface and the complete permanent quality gate.

## Definition of done

- A new user can identify the five-minute path from configuration to a risk-vetted plan and an
  optional explicitly approved Alpaca paper order.
- Product claims distinguish deterministic analysis from prediction and never promise profit or
  accuracy that has not been measured.
- News remains contextual, Myfxbook remains read-only, deterministic risk retains veto authority,
  and all broker order submission remains Alpaca paper-only and manually approved.
- Full pytest, Ruff, strict mypy, JavaScript syntax checks, launcher smoke check, and
  `git diff --check` pass.
- The verified release is tagged `phase90-frozen` and `v1.0.0` and published to remote `main`.

## Non-goals and safety

- No continuous scheduler, automatic order execution, live-money endpoint, profit promise, hidden
  retry, or provider credential is added.
- Finishing v1.0 does not authorize live trading, background deployment, or an LLM to control
  orders.
