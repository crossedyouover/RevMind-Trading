# Open RevMind

Double-click **Start-RevMind.cmd** in this repository. It starts the local server and opens your
default browser at **http://127.0.0.1:8765**. Keep the launcher window open while using RevMind;
close it or press Ctrl+C there to stop the server. It does not install an automatic startup service.

Alternatively, from the repository in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m app.dashboard --open-browser
```

The main action is **Check opportunities**. It retrieves a bounded window of completed historical
bars from the selected Alpaca feed and passes each symbol through RevMind's receipt-aware ingestion,
point-in-time materialization, technical evidence, setup and trend engines. The result cards show
the latest close, trend, active setup, number of analyzed bars and a recent chart.

Use the card labels as a decision queue:

- **READY**: setup, broad-market context, relative strength, held-out evidence, and bar freshness
  passed the readiness gate. Only this state can create a risk plan.
- **CAUTION**: a setup exists but one or more evidence checks did not pass. Inspect the blockers and
  wait; the server will reject paper-plan requests for this assessment.
- **WAIT**: no valid current entry exists, including when the latest completed bar is stale.

The scan header summarizes blockers across the watchlist. Cards are ranked and expandable; the top
cautious candidates and every READY result open by default. A saved result is restored after browser
reload as `LAST SAVED`, but its assessment IDs are removed. Re-scan before creating any current plan.

Every symbol card includes four deterministic comments: **Trend**, **What RevMind sees**,
**Where**, and **What to do**. They describe the 20-bar trend, which breakout/breakdown conditions
are or are not active, the latest close relative to the 20-bar average and prior range, and the
next permitted action. These comments are templates populated from retained evidence; they are not
LLM-generated forecasts.

The separate **Run synthetic demo** action remains an engineering verification tool. Its run
history, audit trail and exported JSON are synthetic and must not be confused with Alpaca research.

## Data settings

Use **Data settings** to select Offline demonstration or Alpaca market data, Alpaca IEX or SIP,
a one-minute/five-minute/fifteen-minute/hour/day timeframe, regular or extended session policy,
and up to 25 exact watchlist identities. Enter identities as `SYMBOL:MIC`; append `:ETF` for an
ETF. Examples are `AAPL:XNAS`, `MSFT:XNAS`, and `SPY:ARCX:ETF`. RevMind does not guess exchanges.

You can enter both Alpaca credential fields and save them locally. They are stored only in
`.revmind/alpaca.env`, which Git ignores; the dashboard never sends them back to the browser or
prints them. Blank fields retain existing credentials. The removal checkbox explicitly deletes
the local credential file when settings are saved. Protect the Windows account and repository
folder: this local file is not encrypted or managed by a secret vault. An Alpaca API key may have
paper-account and paper-order authority. RevMind fixes order traffic to Alpaca's paper host, but you
must still protect the keys and never paste credentials into chat or commit them.

`CONFIGURED NOT ACTIVE` means selections and credentials are saved; a request occurs only when you
explicitly test the connection, analyze the watchlist, refresh headlines, synchronize the paper
account, or start browser-session monitoring.
`CREDENTIALS REQUIRED` means Alpaca was selected without both credentials. `OFFLINE DEMO` means
only synthetic captures are enabled. Feed entitlement and credential validity are not asserted
until a later explicit connection/health-check phase.

The status strip reports dashboard readiness, the selected source, credential/test state,
continuous-streaming status, and that broker execution is restricted to explicit paper approval.

The header mirrors the selected source. `CONNECTED READ ONLY` means the bounded market-data probe
succeeded; it does not mean streaming or automatic execution is enabled. Sidebar highlighting and
visible panels follow the selected workflow section.

After saving Alpaca settings, **Test read-only connection** performs one bounded HTTPS snapshot
request for each configured watchlist identity through the fixed market-data origin
`data.alpaca.markets`. RevMind maps the response through its frozen Alpaca adapter, assigns one
actual UTC receipt boundary to the batch, and appends the canonical observations to
`.revmind/market-observations.db`. The dashboard shows prices, provider event times, and the batch
receipt time; credentials and provider response bodies are never displayed or persisted there.
Every settings save resets the connection claim to `NOT TESTED` until another explicit test.

**Check opportunities** performs separate bounded historical-bar requests. To avoid incomplete
bars and delayed-feed entitlement ambiguity, the end boundary is at least 20 minutes behind the
current UTC clock and aligned to the selected timeframe. Standard lookback is bounded to 7 days for
one-minute, 14 days for five-minute, 30 days for fifteen-minute, 90 days for hourly, and 365 days for
daily bars. Extended validation uses 14, 45, 120, 365, and 1,825 days respectively. The regular-session
rule retains weekday 09:30–16:00 US Eastern bars; extended-hours mode keeps provider-returned bars.

Freshness is a separate readiness veto: M1 bars may be at most 30 minutes old, M5 45 minutes, M15
75 minutes, H1 3 hours, and D1 3 days. This prevents an old intraday setup from becoming READY after
the market closes or over a weekend.

There are no WebSockets, server-side scheduler, or live-money endpoints. Optional monitoring is a
bounded timer in the open browser tab. It never approves or submits orders, and it pauses when every
selected instrument has stale completed bars. A completed result is research evidence, not a
completeness guarantee or promise of market direction.

New demo records are saved in `.dashboard-runs/<UUID>/`. They are not deleted automatically.
Each run has its own observation/capture databases so repeated demos do not exhaust a shared
history limit. Your existing PowerShell demo is read only; it is not modified by the viewer.
Digests are checked when displaying completed records; invalid records are marked unreadable.

Use **Sync paper account** to read cash, equity, buying power, and open positions from the fixed
`paper-api.alpaca.markets` host. For a READY result RevMind selects the evidence-aligned direction,
uses the structural invalidation reference as the proposed stop, synchronizes paper capacity,
calculates a conservative whole-share size from the configured loss/exposure/cash limits, and applies
the deterministic desk and risk vetoes. An eligible result displays entry limit, protective stop,
illustrative two-risk-unit target, projected cash, and an **Approve Alpaca paper order** button. You
may change limits and recalculate, but cannot override a veto. Final confirmation submits one day
limit bracket order to Alpaca Paper Trading. The one-time approval is consumed on use. RevMind never
chooses approval for you, never retries a rejected order, and has no route to `api.alpaca.markets`.

The watchlist cards use real historical Alpaca data when Alpaca is selected; the lower offline-demo
section remains synthetic. The News section displays bounded timestamped Alpaca headlines and
allowlisted official public RSS fallbacks as neutral context only. News never changes setup,
readiness, risk, sizing, or order decisions. Desktop notifications may announce newly READY monitored
setups when the user grants browser permission; external alerts and automatic trade execution remain
unavailable.

The server binds only to 127.0.0.1 and checks Host, Origin, fetch-site and a per-launch API token.
It permits only fixed static files, run reads and the fixed synthetic demo action, not arbitrary
paths, shell commands or uploaded policies. No CORS access, remote control or Angelo OS grants
are enabled. This trusted-local utility is not a hardened multi-user server: do not expose it
through a proxy, tunnel, LAN binding or public port. Local account processes can access it.

If port 8765 is already in use, first try opening the existing dashboard. Otherwise choose another
port with `--port 8766`. The launcher requires this repository's existing `.venv`; no installation
or dependency downloads happen when you double-click it.
