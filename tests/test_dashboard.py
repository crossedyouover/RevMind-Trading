"""Local UI routes cannot select arbitrary files, policies or external destinations."""

import json
import shutil
import sqlite3
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from app.dashboard.server import Dashboard, handler
from app.dashboard.settings import DashboardSettings, SettingsStore


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
        assert b'id="csv-import-form"' in html
        assert b'id="auto-scan"' in html
        assert b'id="desktop-alerts"' in html
        assert b"bounded browser-session monitoring" in html
        assert "frame-ancestors 'none'" in csp
        status, javascript, _ = call("/app.js")
        assert status == 200
        assert b"function allBarsStale(report)" in javascript
        assert b"function openCsvImport()" in javascript
        assert b"Choose your market-data path" in javascript
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
        assert call("/api/alpaca/test", "POST", headers, '{"symbols":["AAPL"]}')[0] == 400
        assert call("/api/alpaca/research", "POST", headers, '{"days":30}')[0] == 400
        assert call("/api/alpaca/news", "POST", headers, '{"days":30}')[0] == 400
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
