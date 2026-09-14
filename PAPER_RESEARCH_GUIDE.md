# Offline paper-risk evaluation over captured research

Phase 27 now has a separate `PAPER_RESEARCH_V1` library interface in `app/capture/paper.py`.
The existing capture CLI and CAPTURE_V1 records remain unchanged. This is local, retrospective
as-of paper research, not a broker simulator, live decision loop or trading authorization.

## Required inputs

Construct `PaperEvaluationRequest` with an explicit UUID4 evaluation ID and all of:

- The complete `SealedInputs` and matching `CycleResult` from the capture step.
- An `ObservedPaperAccountState`, including explicit positions/marks and pending-action state.
- A `PaperRiskProposal`, including quantity change and its reference mark.
- A `PaperPolicy` binding the capture-policy digest to complete frozen `PaperRiskPolicy` and
  `HeadOfDeskPolicy` values. Account, instrument, currency and expected risk policy must match.

No account, proposal, cash amount, reference price or risk limit is generated. Tests use synthetic
values, not recommended operating settings. Missing required inputs are rejected. Future-known
account/mark/proposal inputs are rejected against the sealed cutoff. Stale valid inputs remain
stale and are evaluated by the frozen risk gate; they are never refreshed or replaced implicitly.

The service recomputes materialization/research/trend from the sealed observations and verifies the
capture result and its coverage/freshness policy before composing the paper result. Supplied
receipt times remain caller assertions: these checks do not authenticate a source or prove that
paper inputs supplied later really existed at the historical cutoff. Journal receipt time is
recorded separately; no claim of a historical live decision is made.

## Library use

With a fully constructed request named `request` and an explicit aware processing time named
`recorded_at`, the trusted local host can invoke:

```python
from pathlib import Path
from app.capture.paper import PaperEvaluationCoordinator

coordinator = PaperEvaluationCoordinator(
    Path(".paper-research"),
    allowed_policy_digests=(request.policy.digest(),),
)
try:
    result = coordinator.evaluate(request, recorded_at)
finally:
    coordinator.close()
```

In a real embedding host, authorize policy digests independently of untrusted requests. This
snippet demonstrates local construction, not remote authentication. Processing time must be at
or after capture completion; evaluation/as-of time remains the sealed cutoff. `result.decision`
contains complete frozen risk and desk provenance; `result.journal_key` identifies the durable
decision. A veto produces QUIET. PASS_CHECKS, WATCHLIST and ALERT are not permission to trade.

No catalyst or insider reports are added in this slice; absent is not proof of an empty feed.
No messages, outbox dispatch, fills, portfolio mutations, credentials or network calls occur.

## Recovery and storage

`paper-evaluations.db` durably records each exact request and processing attempt before computation.
`journal.db` contains the frozen append-only decision journal. A crash after journal insertion but
before result commit can safely recompute the same decision; the journal preserves its original
receipt. The result's completed_at is completion of this evaluation, not the journal's first receipt.
Repeating a completed evaluation ID returns the stored result. Changed content under that ID,
revoked policy authority, backwards attempt time and corrupt stored results are rejected.

Failures propagate rather than returning success. Do not delete claims or edit databases to force
a new result. Use a new evaluation ID for genuinely new inputs. Back up the two paper databases
and referenced capture databases together while writers are stopped. They are unencrypted local
research/account records and require appropriate filesystem access controls.

## Offline verification

```powershell
Set-Location "C:\Users\user\Documents\RevMind-Trading"
.\.venv\Scripts\python.exe -m pytest tests/test_capture_paper.py -q
```

Tests exercise all three dispositions through real frozen engines, veto supremacy, missing/future
inputs, stale account behavior, capture-lineage/freshness tampering, authorization, ID conflicts,
corruption, journal failure and journal-before-checkpoint crash recovery. They require no secrets.
Live acquisition, supervision, operational qualification and a Phase 27 freeze remain outstanding.
