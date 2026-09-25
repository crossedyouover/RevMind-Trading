"""Local UI routes cannot select arbitrary files, policies or external destinations."""

import json
import shutil
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from app.accounts.models import (
    AccountFactBatch,
    ExternalPerformanceObservation,
    TradingAccountSnapshot,
)
from app.dashboard.myfxbook_probe import (
    MyfxbookPerformanceRequest,
    probe_myfxbook_accounts,
    probe_myfxbook_facts,
    probe_myfxbook_performance,
    probe_myfxbook_summary,
)
from app.dashboard.server import Dashboard, handler
from app.dashboard.settings import (
    DashboardSettings,
    MyfxbookConnectionProfile,
    SettingsStore,
)


@pytest.fixture
def app(tmp_path):
    (tmp_path / "examples").mkdir()
    shutil.copyfile(Path("examples/capture-demo.json"), tmp_path / "examples/capture-demo.json")
    return Dashboard(tmp_path)


def test_demo_history_and_corruption(app):
    assert app.list_runs() == []
    run = app.run_demo()
    assert run["state"] == "COMPLETE"
    assert len(run["result"]["research"]["request"]["history"]["bars"]) == 3
    assert app.list_runs()[0]["key"] == run["key"]
    assert app.read(run["key"]) == run
    with sqlite3.connect(app.directory(run["key"]) / "capture.db") as db:
        db.execute("UPDATE cycles SET result_digest='corrupt'")
    with pytest.raises(ValueError):
        app.read(run["key"])
    assert app.list_runs()[0]["state"] == "UNREADABLE"


@pytest.mark.parametrize("key", ["../outside", "../../.env", "%2e%2e", "arbitrary"])
def test_paths_are_not_user_selectable(app, key):
    with pytest.raises(ValueError):
        app.directory(key)


def test_settings_and_credentials_are_local_validated_and_never_returned(tmp_path):
    from app.dashboard.settings import DEFAULT_SETTINGS

    store = SettingsStore(tmp_path / ".revmind")
    settings = DEFAULT_SETTINGS
    saved = store.save(
        settings.model_copy(update={"data_mode": "ALPACA", "alpaca_feed": "SIP"}),
        "test-id",
        "test-secret",
        False,
    )
    assert saved["credentials_configured"] is True
    assert saved["integration_status"] == "CONFIGURED_NOT_ACTIVE"
    assert "test-id" not in json.dumps(saved) and "test-secret" not in json.dumps(saved)
    assert "test-secret" in store.secret_path.read_text(encoding="utf-8")
    assert store.settings_path.is_file()
    store.save(settings, None, None, True)
    assert store.credentials_configured() is False


def test_myfxbook_profile_secret_and_clear_lifecycle_is_redacted(tmp_path):
    store = SettingsStore(tmp_path / ".revmind")
    profile = MyfxbookConnectionProfile(
        provider_account_id="account-7", broker_timezone="Europe/Madrid"
    )
    saved = store.save_myfxbook(profile, "mail@example.test", "password-secret", False)
    assert saved == {
        "profile_configured": True,
        "credentials_configured": True,
        "provider_account_id": "account-7",
        "broker_timezone": "Europe/Madrid",
        "integration_status": "CONFIGURED_NOT_ACTIVE",
    }
    assert "mail@example.test" not in json.dumps(saved)
    assert "password-secret" not in json.dumps(saved)
    assert store.load_myfxbook_profile() == profile
    email, password = store.myfxbook_credentials()
    assert email.get_secret_value() == "mail@example.test"
    assert password.get_secret_value() == "password-secret"
    alpaca_before = (store.settings_path.exists(), store.secret_path.exists())
    cleared = store.save_myfxbook(None, None, None, True)
    assert cleared["integration_status"] == "NOT_CONFIGURED"
    assert not store.myfxbook_profile_path.exists() and not store.myfxbook_secret_path.exists()
    assert (store.settings_path.exists(), store.secret_path.exists()) == alpaca_before


def test_myfxbook_partial_state_is_truthful_and_alpaca_is_untouched(tmp_path):
    from app.dashboard.settings import DEFAULT_SETTINGS

    store = SettingsStore(tmp_path / ".revmind")
    store.save(DEFAULT_SETTINGS, "alpaca-id", "alpaca-secret", False)
    alpaca_settings = store.settings_path.read_bytes()
    alpaca_secret = store.secret_path.read_bytes()
    profile = MyfxbookConnectionProfile(provider_account_id="7", broker_timezone="UTC")
    result = store.save_myfxbook(profile, None, None, False)
    assert result["integration_status"] == "INCOMPLETE_CONFIGURATION"
    assert result["credentials_configured"] is False
    assert store.settings_path.read_bytes() == alpaca_settings
    assert store.secret_path.read_bytes() == alpaca_secret


@pytest.mark.parametrize(
    "profile",
    [
        {"schema_version": "1", "provider_account_id": "7", "broker_timezone": "UTC"},
        {"provider_account_id": "", "broker_timezone": "UTC"},
        {"provider_account_id": " 7", "broker_timezone": "UTC"},
        {"provider_account_id": "7\n8", "broker_timezone": "UTC"},
        {"provider_account_id": "7", "broker_timezone": "Not/AZone"},
        {"provider_account_id": "7", "broker_timezone": " UTC"},
    ],
)
def test_myfxbook_profile_rejects_invalid_values(profile):
    with pytest.raises(ValueError):
        MyfxbookConnectionProfile.model_validate(profile)


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("mail@example.test", None),
        (None, "secret"),
        ("", "secret"),
        (" mail@example.test", "secret"),
        ("mail@example.test", "secret\nvalue"),
        ("mail@example.test", "x" * 513),
    ],
)
def test_myfxbook_save_rejects_credentials_without_changing_files(tmp_path, email, password):
    store = SettingsStore(tmp_path / ".revmind")
    profile = MyfxbookConnectionProfile(provider_account_id="7", broker_timezone="UTC")
    with pytest.raises(ValueError) as caught:
        store.save_myfxbook(profile, email, password, False)
    assert str(caught.value) in {
        "both Myfxbook credential fields are required together",
        "invalid Myfxbook credential value",
    }
    assert not store.myfxbook_profile_path.exists()
    assert not store.myfxbook_secret_path.exists()


@pytest.mark.parametrize(
    "payload",
    [
        b"MYFXBOOK_EMAIL=a\nMYFXBOOK_EMAIL=b\nMYFXBOOK_PASSWORD=c\n",
        b"MYFXBOOK_EMAIL=a\n",
        b"MYFXBOOK_EMAIL= a\nMYFXBOOK_PASSWORD=c\n",
        b"OTHER=value\n",
        b"\xff\xfe",
    ],
)
def test_myfxbook_secret_file_fails_closed(tmp_path, payload):
    store = SettingsStore(tmp_path / ".revmind")
    store.directory.mkdir()
    store.myfxbook_secret_path.write_bytes(payload)
    with pytest.raises(ValueError, match="malformed|not configured"):
        store.myfxbook_credentials()


def test_myfxbook_oversized_files_and_conflicting_clear_fail_closed(tmp_path):
    store = SettingsStore(tmp_path / ".revmind")
    store.directory.mkdir()
    store.myfxbook_profile_path.write_bytes(b"{" + b"x" * 4096)
    with pytest.raises(ValueError, match="too large"):
        store.load_myfxbook_profile()
    store.myfxbook_secret_path.write_bytes(b"x" * 2049)
    with pytest.raises(ValueError, match="too large"):
        store.myfxbook_credentials()
    profile = MyfxbookConnectionProfile(provider_account_id="7", broker_timezone="UTC")
    with pytest.raises(ValueError, match="set and clear"):
        store.save_myfxbook(profile, None, None, True)


def test_myfxbook_settings_routes_have_no_provider_or_execution_dependency():
    import inspect

    import app.dashboard.server as server_module

    source = inspect.getsource(server_module)
    assert "MyfxbookAdapter" not in source
    assert "get-my-accounts" not in source


def test_myfxbook_dashboard_controls_are_read_only_and_do_not_persist_secrets():
    html = Path("app/dashboard/static/index.html").read_text(encoding="utf-8")
    javascript = Path("app/dashboard/static/app.js").read_text(encoding="utf-8")

    assert 'id="myfxbook-form"' in html
    assert 'id="myfxbook-account-id"' in html
    assert 'id="myfxbook-timezone"' in html
    assert 'id="myfxbook-email"' in html
    assert 'id="myfxbook-password"' in html
    assert "READ-ONLY — NO ORDERS THROUGH MYFXBOOK" in html
    assert 'api("/api/myfxbook/settings")' in javascript
    assert 'api("/api/myfxbook/settings","POST"' in javascript
    assert 'id="test-myfxbook"' in html
    assert 'api("/api/myfxbook/test","POST")' in javascript
    assert 'id="refresh-myfxbook-summary"' in html
    assert 'api("/api/myfxbook/summary","POST")' in javascript
    assert "function renderMyfxbookSummary(report)" in javascript
    assert "Current point-in-time provider snapshot" in javascript
    assert "not a prediction, signal, recommendation, or live stream" in javascript
    before_click = javascript.split('$("refresh-myfxbook-summary").onclick=', 1)[0]
    assert 'api("/api/myfxbook/summary"' not in before_click
    summary_handler = javascript.split('$("refresh-myfxbook-summary").onclick=', 1)[1].split(
        '$("csv-import-form")', 1
    )[0]
    assert "setInterval" not in summary_handler
    assert "setTimeout" not in summary_handler
    assert 'id="refresh-myfxbook-facts"' in html
    assert 'api("/api/myfxbook/facts","POST")' in javascript
    assert "function renderMyfxbookFacts(report)" in javascript
    assert "RECENT TRANSACTIONS ARE BOUNDED AND INCOMPLETE" in javascript
    facts_before_click = javascript.split('$("refresh-myfxbook-facts").onclick=', 1)[0]
    assert 'api("/api/myfxbook/facts"' not in facts_before_click
    facts_handler = javascript.split('$("refresh-myfxbook-facts").onclick=', 1)[1].split(
        '$("csv-import-form")', 1
    )[0]
    assert "setInterval" not in facts_handler
    assert "setTimeout" not in facts_handler
    assert "/api/paper-order" not in facts_handler
    assert "/cancel" not in facts_handler
    assert 'id="myfxbook-performance-start" type="date" required' in html
    assert 'id="myfxbook-performance-end" type="date" required' in html
    assert 'id="load-myfxbook-performance"' in html
    assert 'api("/api/myfxbook/performance","POST"' in javascript
    assert "function renderMyfxbookPerformance(report)" in javascript
    assert "Past provider-reported performance is context only" in javascript
    assert "No range is selected automatically" in html
    performance_before_submit = javascript.split(
        '$("myfxbook-performance-form").onsubmit=', 1
    )[0]
    assert 'api("/api/myfxbook/performance"' not in performance_before_submit
    performance_handler = javascript.split(
        '$("myfxbook-performance-form").onsubmit=', 1
    )[1].split('$("csv-import-form")', 1)[0]
    assert "setInterval" not in performance_handler
    assert "setTimeout" not in performance_handler
    performance_renderer = javascript.split("function renderMyfxbookPerformance(report)", 1)[
        1
    ].split('$("myfxbook-performance-form").onsubmit=', 1)[0]
    assert "HISTORICAL RANGE · FACTS ONLY" in performance_renderer
    assert "provider-reported daily observation" in performance_renderer
    assert '$("myfxbook-performance-details").open=false' in performance_renderer
    assert "reduce(" not in performance_renderer
    assert "Math." not in performance_renderer
    assert 'data-section="account" href="#account"' in html
    assert 'id="myfxbook-card"' in html
    assert 'id="myfxbook-readiness"' in html
    assert 'id="myfxbook-readiness-title"' in html
    assert 'id="myfxbook-readiness-copy"' in html
    assert 'id="myfxbook-next-action"' in html
    assert 'id="myfxbook-connect-state"' in html
    assert 'id="myfxbook-current-state"' in html
    assert 'id="myfxbook-history-state"' in html
    assert 'id="myfxbook-setup-details"' in html
    assert 'account:"Account Context"' in javascript
    assert '"providers","account","history"' in javascript
    assert 'section==="providers"||section==="account"' in javascript
    assert 'section==="account"' in javascript
    assert "Navigation never contacts Myfxbook" in javascript
    assert html.index('id="myfxbook-connect-step"') < html.index(
        'id="myfxbook-current-step"'
    ) < html.index('id="myfxbook-history-step"')
    assert "Connect your account" in html
    assert "Inspect current exposure" in html
    assert "Review past performance" in html
    connect_step = html.split('id="myfxbook-connect-step"', 1)[1].split(
        'id="myfxbook-current-step"', 1
    )[0]
    current_step = html.split('id="myfxbook-current-step"', 1)[1].split(
        'id="myfxbook-history-step"', 1
    )[0]
    history_step = html.split('id="myfxbook-history-step"', 1)[1].split(
        'class="note"', 1
    )[0]
    assert 'id="myfxbook-form"' in connect_step
    assert 'id="myfxbook-test-result"' in connect_step
    assert 'id="refresh-myfxbook-summary"' in current_step
    assert 'id="refresh-myfxbook-facts"' in current_step
    assert 'id="refresh-myfxbook-current"' in current_step
    assert 'id="myfxbook-current-snapshot"' in current_step
    assert 'id="myfxbook-partial-refresh"' in current_step
    assert 'id="myfxbook-summary-details"' in current_step
    assert 'id="myfxbook-exposure-details"' in current_step
    assert "Account numbers and timestamps" in current_step
    assert "Positions, pending orders, and recent transactions" in current_step
    assert 'id="myfxbook-performance-form"' in history_step
    assert 'id="myfxbook-performance-snapshot"' in history_step
    assert 'id="myfxbook-performance-details"' in history_step
    assert "Provider-reported daily observations" in history_step
    performance_markup = history_step.split('id="myfxbook-performance-details"', 1)[1]
    assert "api(" not in performance_markup
    assert "setInterval" not in performance_markup
    assert "setTimeout" not in performance_markup
    current_handler = javascript.split(
        '$("refresh-myfxbook-current").onclick=', 1
    )[1].split("function renderMyfxbookPerformance", 1)[0]
    assert 'api("/api/myfxbook/summary","POST")' in current_handler
    assert 'api("/api/myfxbook/facts","POST")' in current_handler
    assert "PARTIALLY REFRESHED" in current_handler
    assert "setInterval" not in current_handler
    assert "setTimeout" not in current_handler
    assert "/api/paper-order" not in current_handler
    assert "function renderMyfxbookCurrentSnapshot(summaryReport,factsReport)" in javascript
    assert 'account.balance+" "+account.currency' in javascript
    assert 'account.equity+" "+account.currency' in javascript
    assert 'facts.positions.length' in javascript
    assert 'facts.orders.length' in javascript
    assert "CURRENT ACCOUNT SNAPSHOT · FACTS ONLY" in javascript
    assert '"Summary observed "+account.observed_at' in javascript
    assert '" · exposure observed "+facts.account.observed_at' in javascript
    assert "no score, prediction, recommendation, or action" in javascript
    assert "function clearMyfxbookCurrentSnapshot()" in javascript
    assert "Combined snapshot incomplete" in javascript
    assert "renderMyfxbookCurrentSnapshot(summaryReport,factsReport)" in current_handler
    assert "clearMyfxbookCurrentSnapshot()" in current_handler
    assert "snapshot identity mismatch; results were not combined" in current_handler
    assert "summaryReport.account.provider_account_id" in current_handler
    assert "factsReport.facts.account.provider_account_id" in current_handler
    assert "summaryReport.account.currency" in current_handler
    assert "factsReport.facts.account.currency" in current_handler
    summary_refresh_handler = javascript.split(
        '$("refresh-myfxbook-summary").onclick=', 1
    )[1].split("function myfxbookFactSection", 1)[0]
    facts_refresh_handler = javascript.split(
        '$("refresh-myfxbook-facts").onclick=', 1
    )[1].split("function renderMyfxbookCurrentSnapshot", 1)[0]
    assert '$("myfxbook-summary-details").open=true' in summary_refresh_handler
    assert '$("myfxbook-exposure-details").open=true' in facts_refresh_handler
    partial_refresh = current_step.split('id="myfxbook-partial-refresh"', 1)[1].split(
        'id="myfxbook-current-snapshot"', 1
    )[0]
    assert "Advanced: refresh only part of the account" in partial_refresh
    assert "does not produce the complete snapshot" in partial_refresh
    assert 'id="refresh-myfxbook-summary"' in partial_refresh
    assert 'id="refresh-myfxbook-facts"' in partial_refresh
    primary_controls = current_step.split('id="myfxbook-partial-refresh"', 1)[0]
    assert 'id="refresh-myfxbook-current"' in primary_controls
    assert 'id="refresh-myfxbook-summary"' not in primary_controls
    assert 'id="refresh-myfxbook-facts"' not in primary_controls
    assert "api(" not in partial_refresh
    assert "setInterval" not in partial_refresh
    assert "setTimeout" not in partial_refresh
    detail_markup = current_step.split('id="myfxbook-summary-details"', 1)[1]
    assert "api(" not in detail_markup
    assert "setInterval" not in detail_markup
    assert "setTimeout" not in detail_markup

    readiness_renderer = javascript.split("function renderMyfxbookReadiness()", 1)[1].split(
        '$("myfxbook-next-action").onclick=', 1
    )[0]
    assert "Complete the local setup" in readiness_renderer
    assert "Verify the saved connection" in readiness_renderer
    assert "Load the current account facts" in readiness_renderer
    assert "Current facts are ready to review" in readiness_renderer
    assert 'action.dataset.next="setup"' in readiness_renderer
    assert 'action.dataset.next="test"' in readiness_renderer
    assert 'action.dataset.next="current"' in readiness_renderer
    assert 'action.dataset.next="history"' in readiness_renderer
    assert "api(" not in readiness_renderer
    assert "setInterval" not in readiness_renderer
    assert "setTimeout" not in readiness_renderer
    assert "localStorage" not in readiness_renderer
    assert "sessionStorage" not in readiness_renderer
    next_action = javascript.split('$("myfxbook-next-action").onclick=', 1)[1].split(
        "function showMyfxbookSettings", 1
    )[0]
    assert '$("test-myfxbook").click()' in next_action
    assert '$("refresh-myfxbook-current").click()' in next_action
    assert '$("myfxbook-performance-start").focus()' in next_action
    assert "api(" not in next_action
    setup_markup = connect_step.split('id="myfxbook-setup-details"', 1)[1]
    assert "Setup and connection test" in setup_markup
    assert 'id="myfxbook-form"' in setup_markup
    assert 'id="myfxbook-test-result"' in setup_markup
    assert '$("myfxbook-setup-details").open=true' in next_action
    assert '$("myfxbook-setup-details").open=false' in javascript
    assert "api(" not in setup_markup
    assert "setInterval" not in setup_markup
    assert "setTimeout" not in setup_markup
    step_renderer = javascript.split("function renderMyfxbookStepStates()", 1)[1].split(
        '$("myfxbook-next-action").onclick=', 1
    )[0]
    assert "VERIFIED THIS SESSION" in step_renderer
    assert "SAVED · TEST NEXT" in step_renderer
    assert "SETUP REQUIRED" in step_renderer
    assert "LOADED THIS SESSION" in step_renderer
    assert "INCOMPLETE · RETRY" in step_renderer
    assert "api(" not in step_renderer
    assert "setInterval" not in step_renderer
    assert "setTimeout" not in step_renderer
    assert "localStorage" not in step_renderer
    assert "sessionStorage" not in step_renderer
    assert 'myfxbookCurrentState="LOADING"' in javascript
    assert 'myfxbookCurrentState="COMPLETE"' in javascript
    assert 'myfxbookCurrentState="INCOMPLETE"' in javascript
    assert 'myfxbookHistoryState="LOADING"' in javascript
    assert 'myfxbookHistoryState="COMPLETE"' in javascript
    assert 'myfxbookHistoryState="INCOMPLETE"' in javascript
    invalidation = javascript.split("function invalidateMyfxbookResults()", 1)[1].split(
        '$("myfxbook-next-action").onclick=', 1
    )[0]
    for surface in (
        "myfxbook-test-result",
        "myfxbook-current-snapshot",
        "myfxbook-summary-result",
        "myfxbook-facts-result",
        "myfxbook-performance-snapshot",
        "myfxbook-performance-result",
    ):
        assert f'$("{surface}").replaceChildren' in invalidation
    for disclosure in (
        "myfxbook-summary-details",
        "myfxbook-exposure-details",
        "myfxbook-performance-details",
        "myfxbook-partial-refresh",
    ):
        assert f'$("{disclosure}").open=false' in invalidation
    assert "api(" not in invalidation
    assert "setInterval" not in invalidation
    assert "setTimeout" not in invalidation
    assert "localStorage" not in invalidation
    assert "sessionStorage" not in invalidation
    save_handler = javascript.split('$("myfxbook-form").onsubmit=', 1)[1].split(
        '$("clear-myfxbook").onclick=', 1
    )[0]
    clear_handler = javascript.split('$("clear-myfxbook").onclick=', 1)[1].split(
        "function chooseMyfxbookAccount", 1
    )[0]
    assert "invalidateMyfxbookResults()" in save_handler
    assert "invalidateMyfxbookResults()" in clear_handler
    route_body = javascript.split("function highlightNav", 1)[1].split(
        "for(const link of navLinks)", 1
    )[0]
    assert 'api("/api/myfxbook/' not in route_body
    assert "function chooseMyfxbookAccount(accountId)" in javascript
    assert '$("myfxbook-account-id").value=accountId' in javascript
    assert "nothing was saved automatically" in javascript
    assert "Choose deliberately, then save separately" in javascript
    choice_function = javascript.split("function chooseMyfxbookAccount", 1)[1].split(
        "function renderMyfxbookAccounts", 1
    )[0]
    assert ".submit(" not in choice_function
    assert "requestSubmit" not in choice_function
    assert 'api("' not in choice_function
    assert (
        'window.confirm("Remove the locally stored Myfxbook profile and credentials?")'
        in javascript
    )
    assert '$("myfxbook-email").value=""' in javascript
    assert '$("myfxbook-password").value=""' in javascript
    myfxbook_lines = "\n".join(
        line for line in javascript.splitlines() if "myfxbook" in line.lower()
    )
    assert "localStorage" not in myfxbook_lines
    assert "sessionStorage" not in myfxbook_lines
    assert "state.email" not in javascript
    assert "state.password" not in javascript
    assert "get-my-accounts" not in javascript


@pytest.mark.asyncio
async def test_myfxbook_probe_returns_redacted_accounts_and_disconnects(tmp_path):
    store = SettingsStore(tmp_path / ".revmind")
    profile = MyfxbookConnectionProfile(provider_account_id="7", broker_timezone="UTC")
    store.save_myfxbook(profile, "mail@example.test", "password-secret", False)
    calls: list[str] = []

    class Probe:
        async def list_accounts(self):
            calls.append("list")
            return (
                TradingAccountSnapshot(
                    provider="myfxbook",
                    provider_account_id="7",
                    account_name="Practice context",
                    currency="USD",
                    balance=Decimal("1000"),
                    equity=Decimal("990"),
                    observed_at=datetime(2026, 9, 22, tzinfo=UTC),
                ),
            )

        async def disconnect(self):
            calls.append("disconnect")

    def factory(email, password, timezone):
        assert email.get_secret_value() == "mail@example.test"
        assert password.get_secret_value() == "password-secret"
        assert timezone == "UTC"
        return Probe()

    result = await probe_myfxbook_accounts(store, factory=factory)
    assert calls == ["list", "disconnect"]
    assert result["status"] == "CONNECTED_READ_ONLY"
    assert result["session"] == "DISCONNECTED"
    assert result["execution"] == "NONE"
    serialized = json.dumps(result)
    assert "mail@example.test" not in serialized
    assert "password-secret" not in serialized


@pytest.mark.asyncio
async def test_myfxbook_probe_disconnects_when_discovery_fails(tmp_path):
    store = SettingsStore(tmp_path / ".revmind")
    profile = MyfxbookConnectionProfile(provider_account_id="7", broker_timezone="UTC")
    store.save_myfxbook(profile, "mail@example.test", "password-secret", False)
    calls: list[str] = []

    class Probe:
        async def list_accounts(self):
            calls.append("list")
            raise RuntimeError("scripted discovery failure")

        async def disconnect(self):
            calls.append("disconnect")

    with pytest.raises(RuntimeError, match="scripted discovery failure"):
        await probe_myfxbook_accounts(store, factory=lambda *_: Probe())
    assert calls == ["list", "disconnect"]


@pytest.mark.asyncio
async def test_myfxbook_summary_binds_exact_saved_account_and_disconnects(tmp_path):
    store = SettingsStore(tmp_path / ".revmind")
    profile = MyfxbookConnectionProfile(provider_account_id="chosen", broker_timezone="UTC")
    store.save_myfxbook(profile, "mail@example.test", "password-secret", False)
    calls: list[str] = []

    def account(account_id: str) -> TradingAccountSnapshot:
        return TradingAccountSnapshot(
            provider="myfxbook",
            provider_account_id=account_id,
            currency="USD",
            balance=Decimal("1000"),
            equity=Decimal("990"),
            observed_at=datetime(2026, 9, 22, tzinfo=UTC),
        )

    class Probe:
        async def list_accounts(self):
            calls.append("list")
            return (account("other"), account("chosen"))

        async def disconnect(self):
            calls.append("disconnect")

    result = await probe_myfxbook_summary(store, factory=lambda *_: Probe())
    assert result["account"]["provider_account_id"] == "chosen"
    assert result["session"] == "DISCONNECTED"
    assert result["execution"] == "NONE"
    assert calls == ["list", "disconnect"]
    assert "mail@example.test" not in json.dumps(result)


@pytest.mark.asyncio
async def test_myfxbook_summary_fails_closed_and_disconnects_for_wrong_account(tmp_path):
    store = SettingsStore(tmp_path / ".revmind")
    profile = MyfxbookConnectionProfile(provider_account_id="chosen", broker_timezone="UTC")
    store.save_myfxbook(profile, "mail@example.test", "password-secret", False)
    disconnected = False

    class Probe:
        async def list_accounts(self):
            return ()

        async def disconnect(self):
            nonlocal disconnected
            disconnected = True

    with pytest.raises(ValueError, match="saved Myfxbook account is unavailable"):
        await probe_myfxbook_summary(store, factory=lambda *_: Probe())
    assert disconnected is True


@pytest.mark.asyncio
async def test_myfxbook_fact_probe_binds_saved_id_redacts_and_disconnects(tmp_path):
    store = SettingsStore(tmp_path / ".revmind")
    profile = MyfxbookConnectionProfile(provider_account_id="chosen", broker_timezone="UTC")
    store.save_myfxbook(profile, "mail@example.test", "password-secret", False)
    calls: list[str] = []
    snapshot = TradingAccountSnapshot(
        provider="myfxbook",
        provider_account_id="chosen",
        currency="USD",
        balance=Decimal("1000"),
        equity=Decimal("990"),
        observed_at=datetime(2026, 9, 22, tzinfo=UTC),
    )

    class Probe:
        async def sync_account(self, provider_account_id):
            calls.append("sync:" + provider_account_id)
            return AccountFactBatch(account=snapshot, history_scope="RECENT_INCOMPLETE")

        async def disconnect(self):
            calls.append("disconnect")

    result = await probe_myfxbook_facts(store, factory=lambda *_: Probe())
    assert calls == ["sync:chosen", "disconnect"]
    assert result["facts"]["account"]["provider_account_id"] == "chosen"
    assert result["facts"]["history_scope"] == "RECENT_INCOMPLETE"
    assert result["session"] == "DISCONNECTED"
    assert result["execution"] == "NONE"
    serialized = json.dumps(result)
    assert "mail@example.test" not in serialized
    assert "password-secret" not in serialized


@pytest.mark.asyncio
async def test_myfxbook_fact_probe_disconnects_on_failure(tmp_path):
    store = SettingsStore(tmp_path / ".revmind")
    profile = MyfxbookConnectionProfile(provider_account_id="chosen", broker_timezone="UTC")
    store.save_myfxbook(profile, "mail@example.test", "password-secret", False)
    calls: list[str] = []

    class Probe:
        async def sync_account(self, provider_account_id):
            calls.append("sync:" + provider_account_id)
            raise RuntimeError("scripted fact failure")

        async def disconnect(self):
            calls.append("disconnect")

    with pytest.raises(RuntimeError, match="scripted fact failure"):
        await probe_myfxbook_facts(store, factory=lambda *_: Probe())
    assert calls == ["sync:chosen", "disconnect"]


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "1", "start": "2026-09-01", "end": "2026-09-02"},
        {"schema_version": 1, "start": "2026-09-03", "end": "2026-09-02"},
        {"schema_version": 1, "start": "2025-09-01", "end": "2026-09-02"},
        {"schema_version": 1, "start": "2026-09-01", "end": "2026-09-02", "days": 2},
    ],
)
def test_myfxbook_performance_request_rejects_implicit_or_invalid_ranges(payload):
    with pytest.raises(ValueError):
        MyfxbookPerformanceRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_myfxbook_performance_uses_exact_range_and_disconnects(tmp_path):
    store = SettingsStore(tmp_path / ".revmind")
    profile = MyfxbookConnectionProfile(provider_account_id="chosen", broker_timezone="UTC")
    store.save_myfxbook(profile, "mail@example.test", "password-secret", False)
    request = MyfxbookPerformanceRequest(
        start=date(2026, 9, 1), end=date(2026, 9, 2)
    )
    calls: list[str] = []

    class Probe:
        async def daily_performance(self, provider_account_id, start, end):
            calls.append(f"daily:{provider_account_id}:{start}:{end}")
            return (
                ExternalPerformanceObservation(
                    provider="myfxbook",
                    provider_account_id="chosen",
                    effective_date=date(2026, 9, 2),
                    gain_percent=Decimal("1.25"),
                    observed_at=datetime(2026, 9, 22, tzinfo=UTC),
                ),
            )

        async def disconnect(self):
            calls.append("disconnect")

    result = await probe_myfxbook_performance(store, request, factory=lambda *_: Probe())
    assert calls == ["daily:chosen:2026-09-01:2026-09-02", "disconnect"]
    assert result["start"] == "2026-09-01" and result["end"] == "2026-09-02"
    assert result["observation_count"] == 1
    assert result["session"] == "DISCONNECTED"
    assert "password-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "1"},
        {"watchlist": ()},
        {
            "watchlist": (
                {
                    "symbol": "bad symbol",
                    "exchange": "XNAS",
                    "asset_class": "EQUITY",
                    "currency": "USD",
                },
            )
        },
        {
            "watchlist": (
                {
                    "symbol": "AAPL",
                    "exchange": "NASDAQ",
                    "asset_class": "EQUITY",
                    "currency": "USD",
                },
            )
        },
    ],
)
def test_settings_reject_implicit_or_malformed_instruments(changes):
    from app.dashboard.settings import DEFAULT_SETTINGS

    with pytest.raises(ValueError):
        DashboardSettings.model_validate(DEFAULT_SETTINGS.model_copy(update=changes))


def test_local_session_routes(app):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(app, "test-session"))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(path, method="GET", headers=None, body=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        status, content, csp = (
            response.status,
            response.read(),
            response.getheader("Content-Security-Policy"),
        )
        connection.close()
        return status, content, csp

    try:
        status, html, csp = call("/")
        assert status == 200 and b"test-session" in html
        assert b'data-section="providers"' in html
        assert b'data-section="import"' in html
        assert b'src="/revmind-logo-lockup.png"' in html
        assert b'id="source-badge"' in html
        assert b'id="run-market"' in html
        assert b'id="research-results"' in html
        assert b'id="paper-planner"' in html
        assert b"US STOCKS AND ETFs TO CHECK" in html
        assert b"Automatic headlines:" in html
        assert b'id="news-automatic-sources"' in html
        assert b'id="news-manual-sources"' in html
        assert b"Bloomberg" not in html
        assert b"Benzinga Pro" not in html
        assert b'id="csv-import-form"' in html
        assert b'id="auto-scan"' in html
        assert b'id="desktop-alerts"' in html
        assert b"bounded browser-session monitoring" in html
        assert "frame-ancestors 'none'" in csp
        status, javascript, _ = call("/app.js")
        assert status == 200
        assert b"function allBarsStale(report)" in javascript
        assert b"function openCsvImport()" in javascript
        assert b"What can you safely do now?" in javascript
        assert b"CSV IMPORT AVAILABLE" in javascript
        assert b"Download CSV template" in javascript
        assert b"ALPACA CONFIGURED" in javascript
        assert b'plainNav={desk:"Home"' in javascript
        assert b"READY means the checks passed" in javascript
        assert b'id="page-guide-title"' in javascript
        assert b'id="page-guide-action"' in javascript
        assert b'"Check prices now"' in javascript
        assert b'"Download example CSV"' in javascript
        assert b"Show technical validation and past measurements" in javascript
        assert b'"technical-results"' in javascript
        assert b"Advanced settings" in javascript
        assert b'"advanced-setup"' in javascript
        assert b"YOUR READINESS CHECKLIST" in javascript
        assert b"function updateOperatorChecklist" in javascript
        assert b"Paper plan eligible for review" in javascript
        assert b'window.scrollTo({top:0,behavior:"smooth"})' in javascript
        assert b"TRADE IDEAS" in html
        assert b'querySelectorAll("main > section")' in javascript
        assert b'REVMIND / UPLOAD PRICES' in javascript
        status, stylesheet, _ = call("/style.css")
        assert status == 200 and b"[hidden]{display:none!important}" in stylesheet
        assert (
            b"Monitoring was not started because the latest scan shows every completed bar is stale"
            in javascript
        )
        status, logo, _ = call("/revmind-logo-lockup.png")
        assert status == 200 and logo.startswith(b"\x89PNG\r\n\x1a\n")
        assert call("/api/runs")[0] == 403
        token = {"X-RevMind-Token": "test-session"}
        assert json.loads(call("/api/runs", headers=token)[1]) == []
        assert json.loads(call("/api/paper-orders", headers=token)[1]) == []
        settings = json.loads(call("/api/settings", headers=token)[1])
        assert settings["data_mode"] == "OFFLINE"
        assert "api_secret" not in json.dumps(settings)
        myfxbook = json.loads(call("/api/myfxbook/settings", headers=token)[1])
        assert myfxbook["integration_status"] == "NOT_CONFIGURED"
        assert call("/api/myfxbook/settings")[0] == 403
        unauthenticated_test = call(
            "/api/myfxbook/test", "POST", {"Content-Type": "application/json"}, "{}"
        )
        assert unauthenticated_test[0] == 403
        health = json.loads(call("/api/health", headers=token)[1])
        assert health["dashboard"] == "READY"
        assert health["live_data"] == "DISABLED"
        assert health["broker_execution"] == "PAPER_ONLY_CONFIRMATION_REQUIRED"
        assert health["credentials"] == "NOT_CONFIGURED"
        assert health["live_probe"]["status"] == "NOT_TESTED"
        assert health["imports"] == {"status": "READY", "count": 0}
        providers = json.loads(call("/api/providers", headers=token)[1])
        assert providers["schema_version"] == 1
        assert providers["providers"][0]["provider_id"] == "alpaca"
        assert providers["providers"][0]["execution"] == "PAPER_EXPLICIT_APPROVAL"
        assert all(
            provider["execution"] == "NONE" for provider in providers["providers"][1:]
        )
        assert call("/", headers={"Host": "attacker.example"})[0] == 403
        assert call("/", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert call("/api/runs", headers={**token, "Origin": "https://attacker.example"})[0] == 403
        assert call("/../.env")[0] == 404
        assert call("/api/demo", "POST", {"Content-Type": "application/json"}, "{}")[0] == 403
        headers = {**token, "Content-Type": "application/json"}
        assert call("/api/myfxbook/test", "POST", headers, "{}")[0] == 400
        assert call("/api/myfxbook/test", "POST", headers, '{"account":"7"}')[0] == 400
        assert call("/api/myfxbook/summary", "POST", headers, "{}")[0] == 400
        assert call("/api/myfxbook/summary", "POST", headers, '{"account":"7"}')[0] == 400
        assert call("/api/myfxbook/facts", "POST", headers, "{}")[0] == 400
        assert call("/api/myfxbook/facts", "POST", headers, '{"account":"7"}')[0] == 400
        assert call("/api/myfxbook/performance", "POST", headers, "{}")[0] == 400
        assert call(
            "/api/myfxbook/performance",
            "POST",
            headers,
            '{"schema_version":1,"start":"2026-09-02","end":"2026-09-01"}',
        )[0] == 400
        assert call("/api/demo", "POST", headers, '{"path":".env"}')[0] == 400
        status, result, _ = call("/api/demo", "POST", headers, "{}")
        assert status == 200 and json.loads(result)["state"] == "COMPLETE"
        assert len(json.loads(call("/api/runs", headers=token)[1])) == 1
        settings["data_mode"] = "ALPACA"
        envelope = {
            "settings": {
                k: v
                for k, v in settings.items()
                if k not in {"credentials_configured", "integration_status"}
            },
            "api_key_id": "route-id",
            "api_secret_key": "route-secret",
            "clear_credentials": False,
        }
        status, saved, _ = call("/api/settings", "POST", headers, json.dumps(envelope))
        assert status == 200 and b"route-secret" not in saved
        assert json.loads(saved)["integration_status"] == "CONFIGURED_NOT_ACTIVE"
        assert call("/api/settings", "POST", headers, '{"settings":{}}')[0] == 400
        myfxbook_envelope = {
            "schema_version": 1,
            "provider_account_id": "account-7",
            "broker_timezone": "Europe/Madrid",
            "email": "mail@example.test",
            "password": "route-password-secret",
            "clear_connection": False,
        }
        status, myfxbook_saved, _ = call(
            "/api/myfxbook/settings", "POST", headers, json.dumps(myfxbook_envelope)
        )
        assert status == 200
        assert b"route-password-secret" not in myfxbook_saved
        assert b"mail@example.test" not in myfxbook_saved
        assert json.loads(myfxbook_saved)["integration_status"] == "CONFIGURED_NOT_ACTIVE"
        preserved = {**myfxbook_envelope, "email": None, "password": None}
        status, preserved_body, _ = call(
            "/api/myfxbook/settings", "POST", headers, json.dumps(preserved)
        )
        assert status == 200
        assert json.loads(preserved_body)["credentials_configured"] is True
        assert call(
            "/api/myfxbook/settings",
            "POST",
            headers,
            json.dumps({**myfxbook_envelope, "unexpected": True}),
        )[0] == 400
        assert call(
            "/api/myfxbook/settings",
            "POST",
            headers,
            json.dumps({**myfxbook_envelope, "broker_timezone": "Not/AZone"}),
        )[0] == 400
        assert call(
            "/api/myfxbook/settings",
            "POST",
            headers,
            json.dumps({**myfxbook_envelope, "password": None}),
        )[0] == 400
        assert call("/api/myfxbook/settings", "POST", headers, "not-json")[0] == 400
        assert call(
            "/api/myfxbook/settings",
            "POST",
            headers,
            json.dumps({**myfxbook_envelope, "schema_version": "1"}),
        )[0] == 400
        assert call(
            "/api/myfxbook/settings",
            "POST",
            headers,
            json.dumps({**myfxbook_envelope, "clear_connection": True}),
        )[0] == 400
        assert call(
            "/api/myfxbook/settings",
            "POST",
            headers,
            json.dumps({**myfxbook_envelope, "padding": "x" * 33_000}),
        )[0] == 400
        assert json.loads(call("/api/myfxbook/settings", headers=token)[1])[
            "integration_status"
        ] == "CONFIGURED_NOT_ACTIVE"
        clear_myfxbook = {
            "schema_version": 1,
            "provider_account_id": None,
            "broker_timezone": None,
            "email": None,
            "password": None,
            "clear_connection": True,
        }
        status, cleared_body, _ = call(
            "/api/myfxbook/settings", "POST", headers, json.dumps(clear_myfxbook)
        )
        assert status == 200
        assert json.loads(cleared_body)["integration_status"] == "NOT_CONFIGURED"
        assert call("/api/alpaca/test", "POST", headers, '{"symbols":["AAPL"]}')[0] == 400
        assert call("/api/alpaca/research", "POST", headers, '{"days":30}')[0] == 400
        assert call("/api/alpaca/news", "POST", headers, '{"days":30}')[0] == 400
        assert call("/api/news", "POST", headers, '{"days":30}')[0] == 400
        assert call("/api/alpaca/paper-account", "POST", headers, '{"live":true}')[0] == 400
        import_envelope = {
            "request": {
                "schema_version": 1,
                "symbol": "EURUSD",
                "asset_class": "FX",
                "exchange": "IDEALPRO",
                "currency": "USD",
                "timeframe": "FIVE_MINUTES",
                "source_name": "manual-export",
            },
            "csv_text": (
                "timestamp,open,high,low,close,volume\n"
                "2026-09-12T10:00:00Z,1.1,1.2,1.0,1.15,100\n"
            ),
        }
        status, imported, _ = call(
            "/api/import/csv-bars", "POST", headers, json.dumps(import_envelope)
        )
        imported_body = json.loads(imported)
        assert status == 200
        assert imported_body["status"] == "IMPORTED_RESEARCH_ONLY"
        assert imported_body["paper_execution"] == "UNAVAILABLE_FOR_IMPORTED_DATA"
        assert imported_body["bar_count"] == 1
        assert imported_body["analyzed_bar_count"] == 1
        assert imported_body["research"]["symbol"] == "EURUSD"
        assert imported_body["research"]["assessment_id"] is None
        assert imported_body["research"]["readiness"] == "WAIT"
        assert "observations" not in imported_body
        imports = json.loads(call("/api/imports", headers=token)[1])
        assert len(imports) == 1
        assert imports[0]["symbol"] == "EURUSD"
        assert imports[0]["content_digest"] == imported_body["content_digest"]
        health = json.loads(call("/api/health", headers=token)[1])
        assert health["imports"] == {"status": "READY", "count": 1}
        assert call("/api/import/csv-bars", "POST", headers, "{}")[0] == 400
        assert call("/api/paper-plan", "POST", headers, "{}")[0] == 400
        assert call("/api/paper-order", "POST", headers, "{}")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
