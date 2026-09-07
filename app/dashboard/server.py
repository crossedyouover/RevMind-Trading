"""Small local UI host. Only a fixed synthetic demo can be executed."""

import argparse
import asyncio
import hmac
import json
import secrets
import sqlite3
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from app.capture.__main__ import SimulatedClock
from app.capture.coordinator import OfflineCaptureCoordinator
from app.capture.models import CycleRequest, CycleResult, SealedInputs, digest
from app.dashboard.live import DashboardLiveData, LiveProbeError, PaperPlanInput
from app.dashboard.settings import DashboardSettings, SettingsStore

POLICY = "2bfebfe92eb5b76469b6da94b8f49714147cf85bc0cb12bbacaf77b66edbbeae"
STATIC = Path(__file__).parent / "static"


class Dashboard:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.runs = self.root / ".dashboard-runs"
        self.settings = SettingsStore(self.root / ".revmind")
        self.live = DashboardLiveData(self.settings)

    def directory(self, key: str) -> Path:
        if key == "existing":
            return self.root / ".capture-demo"
        if str(UUID(key)) != key:
            raise ValueError("invalid run identifier")
        return self.runs / key

    def read(self, key: str) -> dict[str, Any]:
        path = self.directory(key) / "capture.db"
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("run path escapes workspace")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute(
                "SELECT id,state,result,result_digest,sealed,sealed_digest FROM cycles LIMIT 1"
            ).fetchone()
            if row is None:
                raise ValueError("run has no capture record")
            events = db.execute(
                "SELECT stage,at FROM events WHERE cycle_id=? ORDER BY sequence LIMIT 100",
                (row[0],),
            ).fetchall()
        result = None
        if row[1] == "COMPLETE":
            if not isinstance(row[2], str) or not isinstance(row[4], str):
                raise ValueError("missing evidence")
            if len(row[2]) > 10_000_000 or len(row[4]) > 10_000_000:
                raise ValueError("artifact exceeds viewer limit")
            if digest(row[2]) != row[3] or digest(row[4]) != row[5]:
                raise ValueError("artifact integrity check failed")
            seal = SealedInputs.model_validate_json(row[4])
            parsed = CycleResult.model_validate_json(row[2])
            if str(parsed.cycle_id) != row[0] or parsed.sealed_digest != row[5]:
                raise ValueError("artifact binding mismatch")
            if parsed.cycle_id != seal.request.cycle_id:
                raise ValueError("sealed cycle mismatch")
            result = parsed.model_dump(mode="json")
        return {"key": key, "cycle_id": row[0], "state": row[1], "events": events, "result": result}

    def list_runs(self) -> list[dict[str, Any]]:
        keys = []
        if (self.root / ".capture-demo" / "capture.db").is_file():
            keys.append("existing")
        if self.runs.is_dir():
            dirs = sorted(
                (p for p in self.runs.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            keys = [p.name for p in dirs[:49]] + keys
        rows = []
        for key in keys:
            try:
                value = self.read(key)
                result = value.pop("result")
                value["bars"] = (
                    len(result["research"]["request"]["history"]["bars"]) if result else 0
                )
                rows.append(value)
            except (ValueError, OSError, sqlite3.Error):
                rows.append({"key": key, "state": "UNREADABLE", "bars": 0, "events": []})
        return rows

    def health(self) -> dict[str, Any]:
        settings = self.settings.public()
        runs = self.list_runs()
        live_state = self.live.state().model_dump(mode="json")
        return {
            "schema_version": 1,
            "dashboard": "READY",
            "settings": "READABLE",
            "selected_source": settings["data_mode"],
            "credentials": (
                "VALIDATED_AT_LAST_TEST"
                if live_state["status"] == "CONNECTED_READ_ONLY"
                else "CONFIGURED_NOT_VALIDATED"
                if settings["credentials_configured"]
                else "NOT_CONFIGURED"
            ),
            "integration": settings["integration_status"],
            "live_data": "DISABLED",
            "live_probe": live_state,
            "broker_execution": "DISABLED",
            "stored_runs": len(runs),
            "unreadable_runs": sum(row["state"] == "UNREADABLE" for row in runs),
        }

    def run_demo(self) -> dict[str, Any]:
        request = CycleRequest.model_validate_json(
            (self.root / "examples/capture-demo.json").read_bytes()
        )
        if request.policy_digest != POLICY:
            raise ValueError("demo policy differs from the approved synthetic example")
        key = str(uuid4())
        request = CycleRequest.model_validate(request.model_copy(update={"cycle_id": UUID(key)}))
        capture = OfflineCaptureCoordinator(
            self.directory(key),
            clock=SimulatedClock(request.scheduled_at),
            observation_id_factory=uuid4,
            allowed_policy_digests=(POLICY,),
        )
        try:
            asyncio.run(capture.execute(request))
        finally:
            capture.close()
        return self.read(key)


def handler(app: Dashboard, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def reply(self, status: int, body: bytes, mime: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; "
                "style-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                "object-src 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def allowed(self, api: bool) -> bool:
            origin = f"http://127.0.0.1:{self.server.server_port}"  # type: ignore[attr-defined]
            if self.headers.get("Host") != origin.removeprefix("http://"):
                return False
            if self.headers.get("Origin", origin) != origin:
                return False
            if self.headers.get("Sec-Fetch-Site", "none") not in {"none", "same-origin"}:
                return False
            return not api or hmac.compare_digest(self.headers.get("X-RevMind-Token", ""), token)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if not self.allowed(path.startswith("/api/")):
                self.reply(403, b"Local session required", "text/plain")
                return
            try:
                if path == "/api/runs":
                    body = json.dumps(app.list_runs()).encode()
                    self.reply(200, body, "application/json")
                elif path == "/api/settings":
                    self.reply(200, json.dumps(app.settings.public()).encode(), "application/json")
                elif path == "/api/health":
                    self.reply(200, json.dumps(app.health()).encode(), "application/json")
                elif path.startswith("/api/runs/"):
                    body = json.dumps(app.read(path.removeprefix("/api/runs/"))).encode()
                    self.reply(200, body, "application/json")
                elif path in {
                    "/",
                    "/app.js",
                    "/style.css",
                    "/settings.css",
                    "/revmind-logo-lockup.png",
                }:
                    name = "index.html" if path == "/" else path[1:]
                    content = (STATIC / name).read_bytes()
                    if name == "index.html":
                        content = content.replace(b"SESSION_TOKEN", token.encode())
                    mime = {
                        "index.html": "text/html; charset=utf-8",
                        "app.js": "text/javascript",
                        "style.css": "text/css",
                        "settings.css": "text/css",
                        "revmind-logo-lockup.png": "image/png",
                    }[name]
                    self.reply(200, content, mime)
                else:
                    self.reply(404, b"Not found", "text/plain")
            except (ValueError, OSError, sqlite3.Error):
                self.reply(
                    400, b'{"error":"Unable to read or validate this run."}', "application/json"
                )

        def do_POST(self) -> None:
            if not self.allowed(True):
                self.reply(403, b"Local session required", "text/plain")
                return
            if (
                self.path
                not in {
                    "/api/demo",
                    "/api/settings",
                    "/api/alpaca/test",
                    "/api/alpaca/research",
                    "/api/paper-plan",
                }
                or self.headers.get("Content-Type") != "application/json"
            ):
                self.reply(400, b"Invalid request", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                length = -1
            if not 0 <= length <= 32_768:
                self.reply(400, b"Invalid request size", "text/plain")
                return
            payload = self.rfile.read(length)
            try:
                if self.path == "/api/demo":
                    if payload != b"{}":
                        raise ValueError("empty request required")
                    value = app.run_demo()
                elif self.path == "/api/alpaca/test":
                    if payload != b"{}":
                        raise ValueError("empty request required")
                    value = asyncio.run(app.live.probe()).model_dump(mode="json")
                elif self.path == "/api/alpaca/research":
                    if payload != b"{}":
                        raise ValueError("empty request required")
                    value = asyncio.run(app.live.research()).model_dump(mode="json")
                elif self.path == "/api/paper-plan":
                    value = app.live.paper_plan(
                        PaperPlanInput.model_validate_json(payload)
                    ).model_dump(mode="json")
                else:
                    body = json.loads(payload)
                    if not isinstance(body, dict) or set(body) != {
                        "settings",
                        "api_key_id",
                        "api_secret_key",
                        "clear_credentials",
                    }:
                        raise ValueError("invalid settings envelope")
                    value = app.settings.save(
                        DashboardSettings.model_validate(body["settings"]),
                        body["api_key_id"],
                        body["api_secret_key"],
                        body["clear_credentials"],
                    )
                    app.live.invalidate()
                self.reply(200, json.dumps(value).encode(), "application/json")
            except LiveProbeError as exc:
                self.reply(502, json.dumps({"error": str(exc)}).encode(), "application/json")
            except (ValueError, TypeError, json.JSONDecodeError):
                self.reply(400, b'{"error":"Invalid settings or request."}', "application/json")
            except Exception:
                self.reply(
                    500,
                    b'{"error":"Local operation failed; no live request was sent."}',
                    "application/json",
                )

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="RevMind local offline dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), handler(Dashboard(root), secrets.token_hex(32))
    )
    url = f"http://127.0.0.1:{server.server_port}"
    print(
        f"RevMind dashboard: {url}\n"
        "Read-only research and paper planning only. Close this window or press Ctrl+C to stop."
    )
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
