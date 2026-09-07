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

Use the card labels as a review queue:

- **PLAN TRADE**: a frozen descriptive setup is active. Open the planner and enter explicit limits;
  this is not yet permission to submit an order.
- **WAIT**: the latest completed bar has no active frozen setup.
- **NO DATA**: Alpaca returned no completed bars in the bounded window.

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
explicitly test the connection or analyze the watchlist.
`CREDENTIALS REQUIRED` means Alpaca was selected without both credentials. `OFFLINE DEMO` means
only synthetic captures are enabled. Feed entitlement and credential validity are not asserted
until a later explicit connection/health-check phase.

The status strip reports dashboard readiness, the selected source, credential/test state,
continuous-streaming status, and that broker execution is restricted to explicit paper approval.

The header mirrors the selected source but keeps `LIVE DISABLED` visible for Alpaca until an
explicit provider-activation phase succeeds. Sidebar highlighting follows the section selected
through its navigation links.

After saving Alpaca settings, **Test read-only connection** performs one bounded HTTPS snapshot
request for each configured watchlist identity through the fixed market-data origin
`data.alpaca.markets`. RevMind maps the response through its frozen Alpaca adapter, assigns one
actual UTC receipt boundary to the batch, and appends the canonical observations to
`.revmind/market-observations.db`. The dashboard shows prices, provider event times, and the batch
receipt time; credentials and provider response bodies are never displayed or persisted there.
Every settings save resets the connection claim to `NOT TESTED` until another explicit test.

**Check opportunities** performs separate bounded historical-bar requests. To avoid incomplete
bars and delayed-feed entitlement ambiguity, the end boundary is at least 20 minutes behind the
current UTC clock and aligned to the selected timeframe. The lookback is bounded by timeframe:
7 days for one-minute, 14 days for five-minute, 30 days for fifteen-minute, 90 days for hourly,
and 365 days for daily bars. Provider-returned sessions are analyzed as received; the saved session
preference is not yet an enforced exchange-calendar filter.

There are no WebSockets, background polling, scheduling, or live-money endpoints. A completed
result is research evidence, not a completeness guarantee or trading signal.

New demo records are saved in `.dashboard-runs/<UUID>/`. They are not deleted automatically.
Each run has its own observation/capture databases so repeated demos do not exhaust a shared
history limit. Your existing PowerShell demo is read only; it is not modified by the viewer.
Digests are checked when displaying completed records; invalid records are marked unreadable.

Use **Sync paper account** to read cash, equity, buying power, and open positions from the fixed
`paper-api.alpaca.markets` host. A PLAN TRADE card opens the deterministic planner. Enter direction,
quantity, stop, maximum loss, trade/exposure caps, concentration, and cash floor. A risk veto cannot
be overridden. An eligible result displays entry limit, protective stop, illustrative two-risk-unit
target, projected cash, and an **Approve Alpaca paper order** button. That button shows a final
confirmation and submits one day limit bracket order to Alpaca Paper Trading. The one-time approval
is consumed on use. RevMind never chooses approval for you, never retries a rejected order, and has
no route to `api.alpaca.markets`.

The watchlist cards use real historical Alpaca data when Alpaca is selected; the lower offline-demo
section remains synthetic. External alerts and automatic trade selection remain unavailable.

The server binds only to 127.0.0.1 and checks Host, Origin, fetch-site and a per-launch API token.
It permits only fixed static files, run reads and the fixed synthetic demo action, not arbitrary
paths, shell commands or uploaded policies. No CORS access, remote control or Angelo OS grants
are enabled. This trusted-local utility is not a hardened multi-user server: do not expose it
through a proxy, tunnel, LAN binding or public port. Local account processes can access it.

If port 8765 is already in use, first try opening the existing dashboard. Otherwise choose another
port with `--port 8766`. The launcher requires this repository's existing `.venv`; no installation
or dependency downloads happen when you double-click it.
