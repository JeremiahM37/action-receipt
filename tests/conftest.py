"""Shared test scaffolding for every tier.

* ``base_url`` - a hermetic HTTP server for ``tests/fixtures/`` (with the ``/slow``, ``/items``,
  ``/submit``, ``/nolen``, ``/file.bin`` and ``/spa/*`` endpoints the fixture pages use) that
  also serves the benchmark fixtures at ``/bench/`` (plus their ``/fail`` and ``/echo``
  endpoints), so the e2e tier can drive the bench apps without importing ``bench``.
* ``validate_all`` - validates every receipt a session produced against the receipt schema;
  the integration tier's ``session`` fixture (``tests/integration/conftest.py``) calls it at
  teardown, so a schema regression fails whichever test produced it.
* ``own_chromium`` + the ``cdp_*`` helpers - a Chromium launched by the test with a remote
  debugging port, for the CDP-attach and wrap-mode flows of both browser tiers.
* Tier markers are assigned from the directory a test lives in (``tests/unit`` -> ``unit``,
  ``tests/integration`` -> ``integration``, ``tests/e2e`` -> ``e2e``), so ``-m <tier>``
  selects a tier without every test carrying a decorator.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from action_receipt.schema import validate_receipt
from action_receipt.session import ReceiptSession

TESTS_DIR = Path(__file__).parent
FIXTURES = TESTS_DIR / "fixtures"
BENCH_FIXTURES = TESTS_DIR.parent / "bench" / "fixtures"

TIER_DIRS = ("unit", "integration", "e2e")


def pytest_collection_modifyitems(config, items):
    """Mark every test with the tier of the directory it lives in."""
    for item in items:
        rel = Path(str(item.fspath)).resolve()
        try:
            parts = rel.relative_to(TESTS_DIR.resolve()).parts
        except ValueError:
            continue
        tier = parts[0] if parts and parts[0] in TIER_DIRS else None
        if tier is None:
            # Files still at the top level of tests/ (during the re-layout): the MCP round trip
            # is e2e, everything else drives a browser against the fixtures.
            tier = "e2e" if rel.name == "test_mcp.py" else "integration"
        item.add_marker(getattr(pytest.mark, tier))


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(FIXTURES), **kw)

    def log_message(self, *a):  # silence
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path == "/slow":
            ms = int(q.get("ms", ["1000"])[0])
            time.sleep(ms / 1000)
            return self._send(200, f"slow response after {ms}ms".encode(), "text/plain")
        if u.path == "/items":
            start, n, ms = (
                int(q.get("from", ["0"])[0]),
                int(q.get("n", ["10"])[0]),
                int(q.get("ms", ["0"])[0]),
            )
            time.sleep(ms / 1000)
            return self._send(
                200, json.dumps([{"i": i} for i in range(start, start + n)]).encode(), "application/json"
            )
        if u.path == "/file.bin":
            return self._send(
                200,
                b"\0" * 4096,
                "application/octet-stream",
                {"Content-Disposition": 'attachment; filename="report.bin"'},
            )
        if u.path == "/submit":
            return self._send(405, b"POST only", "text/plain")
        if u.path == "/nolen":
            # No Content-Length: the body ends when the (HTTP/1.0) connection closes.
            ms = int(q.get("ms", ["0"])[0])
            time.sleep(ms / 1000)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"no content-length here")
            return
        if u.path == "/fail":
            # bench apps: a response with HTTP status S after N ms (the optimistic-revert app)
            ms, status = int(q.get("ms", ["0"])[0]), int(q.get("status", ["500"])[0])
            time.sleep(ms / 1000)
            return self._send(
                status, json.dumps({"ok": status < 400, "status": status}).encode(), "application/json"
            )
        if u.path == "/echo":
            return self._send(200, json.dumps(q).encode(), "application/json")
        if u.path.startswith("/spa/"):
            # SPA deep links resolve to the app shell (what a real SPA server does)
            self.path = "/spa.html"
        if u.path.startswith("/bench/"):
            self.directory = str(BENCH_FIXTURES)
            self.path = self.path[len("/bench") :]
        return super().do_GET()

    def do_POST(self):
        u = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if u.path == "/submit":
            return self._send(
                400,
                b"<html><head><title>Bad Request</title></head><body><h1>400 Bad Request</h1><p>q is invalid</p></body></html>",
                "text/html",
            )
        if u.path.startswith("/bench/") or u.path == "/echo":
            return self._send(200, b'{"ok":true}', "application/json")
        return self._send(404, b"not found", "text/plain")

    def send_header(self, keyword, value):
        # Without the charset Chromium decodes the fixtures as windows-1252 ("FranÃ§ais").
        if keyword.lower() == "content-type" and value == "text/html":
            value = "text/html; charset=utf-8"
        super().send_header(keyword, value)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A page navigating away mid-/slow response closes the socket; expected, not an error.
        pass


@pytest.fixture(scope="session")
def base_url():
    srv = _Server(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


def validate_all(session: ReceiptSession) -> int:
    """Validate every receipt the session produced; returns how many were checked."""
    for r in session.receipts:
        d = r.to_dict()
        json.dumps(d)  # must be JSON-serialisable as well as schema-valid
        validate_receipt(d)
    return len(session.receipts)


@pytest_asyncio.fixture
async def own_chromium():
    """A Chromium launched by *this test* with --remote-debugging-port=0 (the port is read back
    from DevToolsActivePort, so nothing collides). Never the user's browser."""
    pw = await async_playwright().start()
    exe = pw.chromium.executable_path
    await pw.stop()
    udd = tempfile.mkdtemp(prefix="ar-cdp-")
    proc = subprocess.Popen(
        [
            exe,
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={udd}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    port_file = os.path.join(udd, "DevToolsActivePort")
    deadline = time.time() + 15
    while time.time() < deadline and not os.path.exists(port_file):
        await asyncio.sleep(0.05)
    assert os.path.exists(port_file), "chromium did not publish DevToolsActivePort"
    port = int(Path(port_file).read_text().splitlines()[0])
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{url}/json/version", timeout=1).read()
            break
        except Exception:
            await asyncio.sleep(0.05)
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(udd, ignore_errors=True)


def cdp_alive(url: str) -> bool:
    try:
        return bool(json.loads(urllib.request.urlopen(f"{url}/json/version", timeout=2).read()))
    except Exception:
        return False


def cdp_targets(url: str) -> dict[str, str]:
    """Page targets as the browser itself reports them (id -> url), independent of any client."""
    data = json.loads(urllib.request.urlopen(f"{url}/json/list", timeout=2).read())
    return {t["id"]: t["url"] for t in data if t.get("type") == "page"}


def cdp_open_tab(url: str, page_url: str) -> str:
    """Open a tab through the browser's own HTTP endpoint (no Playwright involved); returns its target id."""
    req = urllib.request.Request(f"{url}/json/new?{page_url}", method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=5).read())["id"]
