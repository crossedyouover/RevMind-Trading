# Paper validation runbook

This runbook is for paper/shadow validation only. It does not enable live-money trading.

## Start

1. Launch RevMind with `Start-RevMind.cmd`.
2. Open **Help** and use **Set up data**.
3. Begin with **Offline demonstration** to confirm the workflow.
4. For Alpaca Paper Trading, select Alpaca, enter exact `SYMBOL:MIC` identities, save, and run
   **Test read-only connection**.
5. Run **Check opportunities** and record the timestamp, source health, readiness, and blockers.

## Review each cycle

- A `READY` card may open a paper plan; `CAUTION` and `WAIT` are stop states.
- Confirm the latest completed bar is inside the freshness window.
- Confirm the risk panel has no veto and the paper account sync succeeded.
- Treat News as context only. It cannot change the plan or authorize an order.
- Record the operator decision and reason in the Journal.

## Pause and recover

- Stop the browser monitoring action before closing the browser.
- Keep the local journal and observation database intact.
- Restart the dashboard and inspect History before resuming.
- A recovered run must not create duplicate cycles, alerts, or paper orders.

## End-of-window report

Export the journal and summarize coverage, stale or missing data, risk vetoes, paper outcomes,
operator approvals, rejected requests, and unresolved limitations. Do not infer live profitability
from this report. Continue paper validation, revise the policy, or stop based on the evidence.
