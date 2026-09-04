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
        assert "frame-ancestors 'none'" in csp
        assert call("/api/runs")[0] == 403
        token = {"X-RevMind-Token": "test-session"}
        assert json.loads(call("/api/runs", headers=token)[1]) == []
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
