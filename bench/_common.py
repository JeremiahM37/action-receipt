"""Shared scaffolding for the benchmark suite: a hermetic fixture server, browser/session
helpers, statistics (median / p90 / Wilson), environment capture and result writers.

Nothing here touches the library's own code; the bench only *uses* ``action_receipt``.
"""

from __future__ import annotations

import http.server
import json
import os
import platform
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from math import sqrt
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

BENCH_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BENCH_DIR.parent
FIXTURES = BENCH_DIR / "fixtures"
LIB_FIXTURES = PROJECT_DIR / "tests" / "fixtures"
RESULTS = Path(os.environ.get("AR_BENCH_RESULTS") or BENCH_DIR / "results").resolve()
SCRATCH = Path(
    os.environ.get(
        "AR_BENCH_SCRATCH",
        "/tmp/claude-1000/-home-admin--claude-sessions-claude-5/5b561c8d-ae7b-4da1-91b9-2cb3130869f9/scratchpad/a1-bench",
    )
)


# ----------------------------------------------------------------------------- fixture server
class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves bench/fixtures at / and tests/fixtures at /lib/, plus /slow?ms=N (a response that
    takes N ms), /fail?status=S&ms=N (a response with HTTP status S after N ms) and /echo (any query, instant). Every response is Cache-Control: no-store."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(FIXTURES), **kw)

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str = "text/plain", status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/slow":
            ms = int(parse_qs(u.query).get("ms", ["1000"])[0])
            time.sleep(ms / 1000)
            return self._send(f"slow response after {ms}ms".encode())
        if u.path == "/fail":
            qs = parse_qs(u.query)
            ms = int(qs.get("ms", ["0"])[0])
            status = int(qs.get("status", ["500"])[0])
            time.sleep(ms / 1000)
            return self._send(
                json.dumps({"ok": status < 400, "status": status}).encode(), "application/json", status
            )
        if u.path == "/echo":
            return self._send(json.dumps(parse_qs(u.query)).encode(), "application/json")
        if u.path.startswith("/lib/"):
            self.directory = str(LIB_FIXTURES)
            self.path = self.path[4:]
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(n)
        return self._send(b'{"ok":true}', "application/json")

    def send_header(self, keyword, value):
        # SimpleHTTPRequestHandler sends "Content-type" (lower-case t); without the charset Chromium decodes the
        # fixtures as windows-1252 and "Français" reaches the agent as "FranÃ§ais".
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
        # A page navigating away mid-/slow response closes the socket; that is expected, not an error.
        pass


class FixtureServer:
    def __init__(self):
        self._srv = _Server(("127.0.0.1", 0), _Handler)
        self.port = self._srv.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._srv.shutdown()
        self._srv.server_close()

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *a):
        self.__exit__(*a)

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ----------------------------------------------------------------------------- browser helpers
@asynccontextmanager
async def browser_session(
    *, viewport=(1280, 800), quiet_ms=100.0, timeout_ms=10000.0, screenshots=True, block_external=True
):
    """Headless Chromium + a ReceiptSession attached to a fresh context.

    ``block_external`` aborts every request that is not to 127.0.0.1 - the saved real-page
    fixtures still reference their origin's CSS/images, and the loop must never hit live sites."""
    from playwright.async_api import async_playwright

    from action_receipt.session import ReceiptSession
    from action_receipt.settle import SettleConfig

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
    if block_external:
        await context.route(lambda url: not url.startswith("http://127.0.0.1"), lambda route: route.abort())
    sess = ReceiptSession(
        settle_cfg=SettleConfig(quiet_ms=quiet_ms, timeout_ms=timeout_ms), screenshots=screenshots
    )
    await sess.attach_context(context)
    try:
        yield pw, browser, context, sess
    finally:
        await browser.close()
        await pw.stop()


# ----------------------------------------------------------------------------- statistics
def median(xs: Iterable[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return float("nan")
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def percentile(xs: Iterable[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100)."""
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = max(1, round(p / 100 * len(xs) + 0.5))
    return xs[min(k, len(xs)) - 1]


def p90(xs: Iterable[float]) -> float:
    return percentile(xs, 90)


def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def stdev(xs: Iterable[float]) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score interval for a binomial proportion: (p, lo, hi). n == 0 -> (nan, nan, nan)."""
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def fmt_pct(p: float, digits: int = 1) -> str:
    return "n/a" if p != p else f"{100 * p:.{digits}f}%"


def fmt_wilson(k: int, n: int) -> str:
    p, lo, hi = wilson(k, n)
    if n == 0:
        return "n/a (n=0)"
    return f"{100 * p:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}] ({k}/{n})"


# ----------------------------------------------------------------------------- environment
def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:
        return f"unavailable ({type(e).__name__})"


def lib_hash() -> str:
    """SHA-1 (12 hex) over action_receipt/*.py - the library is being edited concurrently, so every
    result records exactly which source it measured."""
    import hashlib

    h = hashlib.sha1()
    for p in sorted((PROJECT_DIR / "action_receipt").glob("*.py")):
        h.update(p.name.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\n")
    return h.hexdigest()[:12]


def env_info(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    import playwright

    try:
        from importlib.metadata import version as _v

        pw_ver = _v("playwright")
        mcp_ver = _v("mcp")
    except Exception:
        pw_ver = getattr(playwright, "__version__", "?")
        mcp_ver = "?"
    cpu = ""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    mem_gb = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    mem_gb = round(int(line.split()[1]) / 1024 / 1024, 1)
                    break
    except Exception:
        pass
    info = {
        "date_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "playwright": pw_ver,
        "mcp": mcp_ver,
        "platform": platform.platform(),
        "kernel": platform.release(),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "mem_gb": mem_gb,
        "git_head": _run(["git", "-C", str(PROJECT_DIR), "rev-parse", "--short", "HEAD"]),
        "lib_hash": lib_hash(),
        "git_dirty": bool(
            _run(["git", "-C", str(PROJECT_DIR), "status", "--porcelain", "--", "action_receipt", "tests"])
        ),
        "loadavg": _run(["cat", "/proc/loadavg"]),
    }
    if extra:
        info.update(extra)
    return info


async def chromium_version() -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True)
        v = b.version
        await b.close()
        return v


# ----------------------------------------------------------------------------- output
def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(str(h) for h in headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def write_results(name: str, data: dict[str, Any], markdown: str) -> tuple[Path, Path]:
    RESULTS.mkdir(parents=True, exist_ok=True)
    jp = RESULTS / f"{name}.json"
    mp = RESULTS / f"{name}.md"
    jp.write_text(json.dumps(data, indent=1, default=str))
    mp.write_text(markdown.rstrip() + "\n")
    return jp, mp


def env_md(info: dict[str, Any]) -> str:
    keys = [
        "date_utc",
        "chromium",
        "playwright",
        "mcp",
        "python",
        "cpu",
        "cpu_count",
        "mem_gb",
        "kernel",
        "git_head",
        "git_dirty",
        "lib_hash",
        "loadavg",
        "model",
        "model_endpoint",
    ]
    rows = [[k, info[k]] for k in keys if k in info]
    return md_table(["env", "value"], rows)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
