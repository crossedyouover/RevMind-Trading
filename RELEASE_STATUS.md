# RevMind Trading v1.0 — Release Status

## What “finished” means

RevMind v1.0 is finished as a **local decision-support and paper-trading application**. It gives an
operator a repeatable path from market data to an explainable, risk-vetted plan and, only after a
separate explicit confirmation, an Alpaca **paper** bracket order.

It is not a profit guarantee, a high-precision prediction oracle, an autonomous trading bot, or a
real-money execution system. A result can be `WAIT` or `BLOCKED`; that is a valid safety outcome.

## Practical workflow

1. Double-click `Start-RevMind.cmd`.
2. Open **Account & data**, choose Alpaca paper/data settings, save them, and run the connection
   check. Credentials remain local and the UI returns redacted status.
3. Open **Find trades**, choose the instruments and timeframe, then run the scan.
4. Review the generated plan: direction, timeframe, entry condition, invalidation/stop, targets,
   evidence, confidence limitations, news context, and every risk warning.
5. If the result is actionable, review it again in **Paper trade**. Order submission requires an
   explicit operator confirmation and can reach only Alpaca's fixed paper-trading endpoint.
6. Use **Journal** to inspect what the system knew and recorded. Use **Myfxbook** only to review the
   configured account's read-only exposure and provider-reported history.

See [DASHBOARD_GUIDE.md](DASHBOARD_GUIDE.md) for the screen-by-screen guide.

## Shipped capability

- Provider-neutral canonical market observations with point-in-time-safe storage and replay.
- Deterministic indicators, evidence, setup composition, multi-instrument scanning, and explicit
  timeframe handling.
- Deterministic risk policy with unconditional veto authority.
- On-demand Alpaca market-data checks and historical-bar research.
- Read-only Alpaca paper-account context and explicitly confirmed paper bracket orders.
- Read-only Myfxbook account, exposure, transaction, and daily-performance review.
- Neutral market-news context with provenance; news never becomes an execution signal.
- Durable audit/evaluation records, synthetic verification, and local shadow/control contracts.
- A loopback-only dashboard with local-session request checks and locally stored secrets.

## Deliberate boundaries

- No real-money trading endpoint.
- No automatic order placement, modification, or cancellation.
- No server-side continuous market ingestion or unattended scheduler.
- No claim that a score, plan, headline, or model predicts future price correctly.
- No LLM authority over risk or execution.
- No silent repair, source blending, fabricated evidence, or look-ahead data.

These are release safety properties, not missing setup steps.

## Release acceptance

The v1.0 source release is accepted only when the complete pytest suite, Ruff, strict mypy,
JavaScript syntax checks, launcher smoke check, and Git whitespace validation all pass on the exact
tagged commit. The canonical tags are `phase90-frozen` and `v1.0.0`.
