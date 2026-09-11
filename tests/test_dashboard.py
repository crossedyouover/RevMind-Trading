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
        assert b'src="/revmind-logo-lockup.png"' in html
        assert b'id="source-badge"' in html
        assert b'id="run-market"' in html
        assert b'id="research-results"' in html
        assert b'id="paper-planner"' in html
        assert b'id="auto-scan"' in html
        assert b"bounded browser-session monitoring" in html
        assert "frame-ancestors 'none'" in csp
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
        assert call("/api/alpaca/paper-account", "POST", headers, '{"live":true}')[0] == 400
        assert call("/api/paper-plan", "POST", headers, "{}")[0] == 400
        assert call("/api/paper-order", "POST", headers, "{}")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
