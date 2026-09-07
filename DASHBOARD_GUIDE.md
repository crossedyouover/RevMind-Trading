# Open RevMind

Double-click **Start-RevMind.cmd** in this repository. It starts the local server and opens your
default browser at **http://127.0.0.1:8765**. Keep the launcher window open while using RevMind;
close it or press Ctrl+C there to stop the server. It does not install an automatic startup service.

Alternatively, from the repository in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m app.dashboard --open-browser
```

The main action is **Analyze my watchlist**. It retrieves a bounded window of completed historical
bars from the selected Alpaca feed and passes each symbol through RevMind's receipt-aware ingestion,
point-in-time materialization, technical evidence, setup and trend engines. The result cards show
the latest close, trend, active setup, number of analyzed bars and a recent chart.

Use the card labels as a review queue:

- **REVIEW**: a frozen descriptive setup is active. Inspect it and apply separate risk judgment;
  this is not an instruction to buy or sell.
- **WAIT**: the latest completed bar has no active frozen setup.
- **NO DATA**: Alpaca returned no completed bars in the bounded window.

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
more account authority than this read-only application uses, so configure provider-side permissions
appropriately and never paste credentials into chat or commit them.

`CONFIGURED NOT ACTIVE` means selections and credentials are saved; a request occurs only when you
explicitly test the connection or analyze the watchlist.
`CREDENTIALS REQUIRED` means Alpaca was selected without both credentials. `OFFLINE DEMO` means
only synthetic captures are enabled. Feed entitlement and credential validity are not asserted
until a later explicit connection/health-check phase.

The status strip reports dashboard readiness, the selected source, credential/test state, and the
hard-disabled state of continuous streaming and broker execution.

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

**Analyze my watchlist** performs separate bounded historical-bar requests. To avoid incomplete
bars and delayed-feed entitlement ambiguity, the end boundary is at least 20 minutes behind the
current UTC clock and aligned to the selected timeframe. The lookback is bounded by timeframe:
7 days for one-minute, 14 days for five-minute, 30 days for fifteen-minute, 90 days for hourly,
and 365 days for daily bars. Provider-returned sessions are analyzed as received; the saved session
preference is not yet an enforced exchange-calendar filter.

There are no WebSockets, background polling, scheduling, broker-account reads, order endpoints, or
execution. A completed result is research evidence, not a completeness guarantee or trading signal.

New demo records are saved in `.dashboard-runs/<UUID>/`. They are not deleted automatically.
Each run has its own observation/capture databases so repeated demos do not exhaust a shared
history limit. Your existing PowerShell demo is read only; it is not modified by the viewer.
Digests are checked when displaying completed records; invalid records are marked unreadable.

The watchlist cards use real historical Alpaca data when Alpaca is selected; the lower offline-demo
section remains synthetic. The dashboard does not yet invoke paper-risk evaluation, read an account,
send alerts or place orders. Paper-risk integration remains available through the separately
documented library interface.

The server binds only to 127.0.0.1 and checks Host, Origin, fetch-site and a per-launch API token.
It permits only fixed static files, run reads and the fixed synthetic demo action, not arbitrary
paths, shell commands or uploaded policies. No CORS access, remote control or Angelo OS grants
are enabled. This trusted-local utility is not a hardened multi-user server: do not expose it
through a proxy, tunnel, LAN binding or public port. Local account processes can access it.

If port 8765 is already in use, first try opening the existing dashboard. Otherwise choose another
port with `--port 8766`. The launcher requires this repository's existing `.venv`; no installation
or dependency downloads happen when you double-click it.
