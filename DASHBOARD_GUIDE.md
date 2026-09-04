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
