# Open RevMind

Double-click **Start-RevMind.cmd** in this repository. It starts the local server and opens your
default browser at **http://127.0.0.1:8765**. Keep the launcher window open while using RevMind;
close it or press Ctrl+C there to stop the server. It does not install an automatic startup service.

Alternatively, from the repository in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m app.dashboard --open-browser
```

The dashboard displays your existing `.capture-demo` result automatically. Click **Run offline
demo** for a new isolated synthetic run. Select a run in **Run history** to inspect price bars,
setup availability, trend evidence and the audit trail. **Export JSON** downloads the selected
result through your browser. **Refresh** reloads stored history. The list shows at most 50 runs.

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

`CONFIGURED NOT ACTIVE` means selections and credentials are saved but no live request will occur.
`CREDENTIALS REQUIRED` means Alpaca was selected without both credentials. `OFFLINE DEMO` means
only synthetic captures are enabled. Feed entitlement and credential validity are not asserted
until a later explicit connection/health-check phase.

The status strip reports dashboard readiness, the selected source, whether credentials are
locally present (not whether Alpaca has accepted them), and the hard-disabled state of live
data and broker execution. It performs no network authentication and never presents a saved
selection as an active connection.

New demo records are saved in `.dashboard-runs/<UUID>/`. They are not deleted automatically.
Each run has its own observation/capture databases so repeated demos do not exhaust a shared
history limit. Your existing PowerShell demo is read only; it is not modified by the viewer.
Digests are checked when displaying completed records; invalid records are marked unreadable.

This is a completed offline viewer/demo workflow, not a live trading application. All displayed
prices are synthetic. WARMING_UP is expected for calculations requiring more than three bars.
The dashboard does not invoke paper-risk evaluation, connect live data, send alerts or place orders.
Paper-risk integration remains available through the separately documented library interface.

The server binds only to 127.0.0.1 and checks Host, Origin, fetch-site and a per-launch API token.
It permits only fixed static files, run reads and the fixed synthetic demo action, not arbitrary
paths, shell commands or uploaded policies. No CORS access, remote control or Angelo OS grants
are enabled. This trusted-local utility is not a hardened multi-user server: do not expose it
through a proxy, tunnel, LAN binding or public port. Local account processes can access it.

If port 8765 is already in use, first try opening the existing dashboard. Otherwise choose another
port with `--port 8766`. The launcher requires this repository's existing `.venv`; no installation
or dependency downloads happen when you double-click it.
