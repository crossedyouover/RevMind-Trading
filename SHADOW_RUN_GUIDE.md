# Local shadow quick start

This is an offline deterministic demonstration, not live market operation or trading.
The example contains synthetic prices and test policy limits, not recommended risk settings.
It keeps delivery disabled; even when enabled the runtime only uses a local recording sink.
Do not put credentials in manifests or journal files.

Run in Windows PowerShell:

```powershell
Set-Location "C:\Users\user\Documents\RevMind-Trading"
.\.venv\Scripts\python.exe -m app.runtime --directory .shadow-demo register --manifest examples/shadow-demo.json --at 2025-02-01T00:00:00+00:00
.\.venv\Scripts\python.exe -m app.runtime --directory .shadow-demo start --run-id 00000000-0000-4000-8000-000000007000 --at 2025-02-01T00:00:00+00:00
.\.venv\Scripts\python.exe -m app.runtime --directory .shadow-demo tick --run-id 00000000-0000-4000-8000-000000007000 --at 2025-02-01T00:00:00+00:00 --max-jobs 1
.\.venv\Scripts\python.exe -m app.runtime --directory .shadow-demo status --run-id 00000000-0000-4000-8000-000000007000
.\.venv\Scripts\python.exe -m app.runtime --directory .shadow-demo audit --run-id 00000000-0000-4000-8000-000000007000
```

Expected status: COMPLETE, next_index=1, total=1. The journal retains the synthetic decision;
no message or order is sent. Repeating tick does not repeat effects. A completed run cannot restart.
Use a distinct run UUID for a new plan, or inspect the existing run; do not delete audit databases
to force retries. A changed plan with the same run UUID is rejected.

Files runtime.db, journal.db, and outbox.db are stored under the explicit directory. SQLite
databases are ignored by Git, but may contain full account/research information. Back them up as
a consistent set while no runner is writing. No encryption, remote authentication, or secret
management is supplied. Use filesystem permissions appropriate to the account data.

Scheduling is explicit: tick processes only due requests, at most max-jobs. PAUSE takes effect
between tick calls. There is no installed background scheduler, hidden clock, or live data loop.
FAILED or uncertain delivery states need inspection; the runner will not auto-resume them.

The synthetic 1,440-step replay test validates software scheduling/recovery, not a live trading
record. Live data integration, real delivery, extended operational trials, and real-money execution
require separate deployment decisions and are not enabled by these commands.
