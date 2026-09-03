"""Settlement: wait for a *measured* quiescence instead of a blind sleep.

Signals, each with its own quiet condition:

* network     - every request started after dispatch has finished or failed
                (websocket / eventsource / media are excluded: they never finish; a request
                that turned into a download is released when the ``download`` event fires).
                Chromium never reports a response as finished while the page has not read its
                body, and pages routinely ignore bodies (``fetch(url).then(r => r.status)``) -
                so a response whose Content-Length is small (<= ``SMALL_BODY_BYTES``) is
                released as soon as its headers arrive (the body is already buffered), and any
                other response is released ``body_grace_ms`` after its headers if the body has
                still not finished. Both releases are recorded in ``signals.network.released``.
* dom         - no MutationObserver records for ``quiet_ms`` (page clock; top frame plus
                same-origin child frames plus shadow roots). Mutations inside *background*
                nodes - marked by the before-capture as already mutating before the action -
                are counted separately (``dom.background_mutations``) and do not keep the
                page busy; a ticker or live feed therefore settles instead of timing out.
* scroll      - no scroll events for ``quiet_ms``.
* layout      - no layout-shift entries for ``quiet_ms``.
* animations  - ``document.getAnimations()`` has no running entry that *started after dispatch*
                (any same-origin frame). Animations already running at dispatch are background
                and ignored (``animations.background``) - finite or perpetual; an animation the
                action started is waited on until it ends or is removed, even a perpetual one,
                because a spinner the action put up is the page saying "still working". A
                document with no dispatch anchor (after a cross-document navigation) falls back
                to: finite busy, perpetual ignored (``animations.infinite`` counts them).
* timers      - no ``setTimeout`` scheduled after dispatch (by foreground code - not by a
                ticker re-arming itself) with a delay <= ``timer_wait_ms`` is still pending.
                Longer timers are reported in ``timers.long`` and not awaited; intervals the
                action started are reported in ``timers.intervals_started``.
* document    - ``document.readyState == 'complete'`` and the execution context answers.
* navigation  - a main-frame navigation began; wait for its load event and re-poll.

``pre_settle`` runs *before* the before-capture: if the page is still mutating (a post-load
render, or a ticker that has not yet shown enough cadence to be marked background) it waits -
up to ``pre_settle_ms`` - until the foreground DOM has been quiet for ``quiet_ms``, re-marking
background on every poll. The before-capture is then of a quiet page, and a page's permanent
activity is background even when the action comes right after ``load``.

Settlement is *aborted* (not timed out) when the page closes or crashes: the report then says
``aborted: page_closed|page_crashed`` and the receipt falls to ``unknown`` at once instead of
spinning until the timeout.

The receipt records which signal was the *last* to go quiet (``settled_by``), how long
settlement took, and - on timeout - which signals were still busy. The only constant that
survives is ``quiet_ms``: it is the length of the quiet window we insist on observing, not a
guess at how long the page needs.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .inject import INSTALL_JS, PRESETTLE_JS, STATUS_JS

# Resource types that legitimately never "finish".
_STREAMING_TYPES = {"websocket", "eventsource", "media"}

# When several signals were last seen busy in the same poll, this order picks ``settled_by``
# so the value is deterministic run to run.
_SETTLED_BY_PRIORITY = (
    "navigation",
    "network",
    "timers",
    "animations",
    "dom",
    "layout",
    "scroll",
    "document",
)

SETTLED_BY_VALUES = _SETTLED_BY_PRIORITY + ("quiet_window", "page_closed", "page_crashed")

MAX_HTTP_ERRORS = 5
# A response body this small is fully buffered by the time the headers are seen.
SMALL_BODY_BYTES = 65536
MAX_RELEASED_LISTED = 3
MAX_LONG_TIMERS_LISTED = 5
# Elements mutated within this many ms before the action are background (a ticker, a live feed).
BACKGROUND_WINDOW_MS = 1500


@dataclass
class SettleConfig:
    quiet_ms: float = 100.0  # quiet window that dom/scroll/layout must show
    poll_ms: float = 20.0  # polling interval
    timeout_ms: float = 10000.0  # hard cap
    nav_load_timeout_ms: float = 10000.0
    body_grace_ms: float = 1500.0  # how long an answered-but-unfinished response body keeps the network busy
    timer_wait_ms: float = 5000.0  # a setTimeout the action scheduled is awaited if its delay is <= this; longer ones are reported
    pre_settle_ms: float = (
        800.0  # max wait before the before-capture for a mutating page to go quiet / show its background
    )


@dataclass
class SettleReport:
    settled: bool
    elapsed_ms: float
    settled_by: str
    timed_out: bool
    busy_at_timeout: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    polls: int = 0
    aborted: str | None = None  # page_closed | page_crashed

    def to_dict(self) -> dict[str, Any]:
        return {
            "settled": self.settled,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "settled_by": self.settled_by,
            "timed_out": self.timed_out,
            "aborted": self.aborted,
            "busy_at_timeout": self.busy_at_timeout,
            "signals": self.signals,
            "polls": self.polls,
        }


class NetworkTracker:
    """Tracks in-flight requests and HTTP error responses for one page. Attach once per page."""

    def __init__(self, page):
        self.page = page
        self.inflight: dict[Any, float] = {}  # request -> started (perf_counter)
        self.started = 0
        self.finished = 0
        self.failed = 0
        self.http_errors: list[tuple[float, dict[str, Any]]] = []  # (ts, {url, status, method})
        self.responded: dict[Any, float] = {}  # request -> when its headers arrived (body still open)
        self.released: list[
            tuple[float, dict[str, Any]]
        ] = []  # (ts, {url, reason}) requests we stopped waiting for
        page.on("request", self._on_request)
        page.on("requestfinished", self._on_done)
        page.on("requestfailed", self._on_failed)
        page.on("response", self._on_response)
        page.on("download", self._on_download)

    def _on_request(self, req):
        self.started += 1
        self.inflight[req] = time.perf_counter()

    def _on_done(self, req):
        self.finished += 1
        self.inflight.pop(req, None)
        self.responded.pop(req, None)

    def _on_failed(self, req):
        self.failed += 1
        self.inflight.pop(req, None)
        self.responded.pop(req, None)

    def _release(self, req, reason: str):
        self.inflight.pop(req, None)
        self.responded.pop(req, None)
        try:
            url = req.url[:200]
        except Exception:
            url = "?"
        self.released.append((time.perf_counter(), {"url": url, "reason": reason}))
        del self.released[:-50]

    def _on_response(self, resp):
        try:
            req = resp.request
            if resp.status >= 400:
                self.http_errors.append(
                    (
                        time.perf_counter(),
                        {"url": resp.url[:200], "status": resp.status, "method": req.method},
                    )
                )
                del self.http_errors[:-50]
            if req in self.inflight:
                cl = resp.headers.get("content-length")
                size = int(cl) if cl is not None and cl.isdigit() else None
                if size is not None and size <= SMALL_BODY_BYTES:
                    self._release(req, f"headers received, body of {size} bytes is already buffered")
                else:
                    self.responded[req] = time.perf_counter()
        except Exception:
            pass

    def _on_download(self, download):
        # A request that became a download never emits requestfinished; release it.
        try:
            url = download.url
        except Exception:
            return
        for req in list(self.inflight):
            try:
                if req.url == url:
                    self.inflight.pop(req, None)
            except Exception:
                pass

    def pending_since(self, t: float, body_grace_s: float | None = None) -> list[Any]:
        out = []
        now = time.perf_counter()
        for req, started in list(self.inflight.items()):
            if started < t:
                continue
            try:
                rtype = req.resource_type
            except Exception:
                rtype = ""
            if rtype in _STREAMING_TYPES:
                continue
            r_at = self.responded.get(req)
            if r_at is not None and body_grace_s is not None and now - r_at >= body_grace_s:
                self._release(
                    req,
                    f"headers received, body not finished within {round(body_grace_s * 1000)}ms "
                    "(the page probably never reads it)",
                )
                continue
            out.append(req)
        return out

    def http_errors_since(self, t: float) -> list[dict[str, Any]]:
        return [e for ts, e in self.http_errors if ts >= t][:MAX_HTTP_ERRORS]

    def released_since(self, t: float) -> list[dict[str, Any]]:
        return [e for ts, e in self.released if ts >= t][:MAX_RELEASED_LISTED]


class NavTracker:
    """Main-frame navigation + new-page + dialog + console-error + download + crash events with timestamps."""

    def __init__(self, page, context):
        self.page = page
        self.context = context
        self.navigations: list[
            tuple[float, str]
        ] = []  # every main-frame framenavigated (incl. same-document)
        self.document_requests: list[
            tuple[float, str]
        ] = []  # main-frame document requests = cross-document navigations
        self.new_pages: list[tuple[float, Any]] = []
        self.dialogs: list[tuple[float, dict[str, Any]]] = []
        self.console_errors: list[tuple[float, str]] = []
        self.downloads: list[tuple[float, dict[str, Any]]] = []
        self.crashed_at: float | None = None
        self.dialog_action = "accept"
        self._tasks: set[asyncio.Future[None]] = set()
        page.on("framenavigated", self._on_nav)
        page.on("request", self._on_request)
        page.on("dialog", self._on_dialog)
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("download", self._on_download)
        page.on("crash", self._on_crash)
        context.on("page", self._on_page)
        page.on("close", self._on_close)

    def _on_close(self, _page=None):
        # Stop receiving new-page events for a page that no longer exists (they accumulate per
        # tracked page otherwise).
        try:
            self.context.remove_listener("page", self._on_page)
        except Exception:
            pass

    def _on_nav(self, frame):
        if frame == self.page.main_frame:
            self.navigations.append((time.perf_counter(), frame.url))

    def _on_request(self, req):
        try:
            if req.is_navigation_request() and req.frame == self.page.main_frame:
                self.document_requests.append((time.perf_counter(), req.url))
        except Exception:
            pass

    def _on_page(self, page):
        self.new_pages.append((time.perf_counter(), page))

    def _on_console(self, msg):
        if msg.type == "error":
            self.console_errors.append((time.perf_counter(), msg.text[:300]))

    def _on_pageerror(self, err):
        self.console_errors.append((time.perf_counter(), f"pageerror: {str(err)[:300]}"))

    def _on_download(self, download):
        try:
            rec = {"filename": download.suggested_filename, "url": download.url[:200]}
        except Exception:
            rec = {"filename": None, "url": None}
        self.downloads.append((time.perf_counter(), rec))

    def _on_crash(self, _page=None):
        self.crashed_at = time.perf_counter()

    def _on_dialog(self, dialog):
        rec = {"type": dialog.type, "message": dialog.message[:300], "handled": self.dialog_action}
        self.dialogs.append((time.perf_counter(), rec))

        async def _handle():
            try:
                if self.dialog_action == "dismiss":
                    await dialog.dismiss()
                else:
                    await dialog.accept()
            except Exception:
                pass

        task = asyncio.ensure_future(_handle())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def since(self, t: float) -> dict[str, Any]:
        navs = [u for ts, u in self.navigations if ts >= t]
        docs = [u for ts, u in self.document_requests if ts >= t]
        return {
            "navigations": navs,
            # A framenavigated backed by a main-frame document request is a real (cross-document)
            # navigation; pushState/replaceState/hash changes fire framenavigated alone.
            "cross_document_navigations": docs if navs else [],
            "new_pages": [p for ts, p in self.new_pages if ts >= t],
            "dialogs": [d for ts, d in self.dialogs if ts >= t],
            "console_errors": [m for ts, m in self.console_errors if ts >= t],
            "downloads": [d for ts, d in self.downloads if ts >= t],
            "crashed": self.crashed_at is not None,
        }


async def ensure_installed(page) -> bool:
    """Install the observer in the main frame and every same-origin child frame (idempotent)."""
    ok = False
    try:
        ok = bool(await page.evaluate(INSTALL_JS))
    except Exception:
        return False
    try:
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                await fr.evaluate(INSTALL_JS)
            except Exception:
                pass  # cross-origin or detached
    except Exception:
        pass
    return ok


async def pre_settle(page, cfg: SettleConfig | None = None) -> dict[str, Any]:
    """Wait (bounded by ``pre_settle_ms``) until the foreground DOM has been quiet for
    ``quiet_ms``, re-marking background on every poll so a ticker that is mid-cadence gets
    classified before the before-capture. Returns what was observed; never raises."""
    cfg = cfg or SettleConfig()
    t0 = time.perf_counter()
    deadline = t0 + cfg.pre_settle_ms / 1000
    polls, roots, quiet, waited_for = 0, 0, False, None
    while True:
        polls += 1
        try:
            st = await page.evaluate(PRESETTLE_JS, BACKGROUND_WINDOW_MS)
        except Exception:
            break
        if st is None:
            await ensure_installed(page)
            if polls >= 2:
                break
            continue
        roots = st["roots"]
        if not st["loading"] and st["now"] - st["ref"] >= cfg.quiet_ms:
            quiet = True
            break
        waited_for = "document" if st["loading"] else "dom"
        if time.perf_counter() >= deadline:
            break
        await asyncio.sleep(cfg.poll_ms / 1000)
    return {
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "polls": polls,
        "quiet": quiet,
        "waited_for": waited_for,
        "background_roots": roots,
        "max_ms": cfg.pre_settle_ms,
    }


async def settle(
    page,
    net: NetworkTracker,
    nav: NavTracker,
    *,
    t_dispatch: float,
    page_t_dispatch: float | None,
    cfg: SettleConfig | None = None,
    mutations_at_dispatch: int = 0,
    bg_mutations_at_dispatch: int = 0,
) -> SettleReport:
    """Wait for quiescence after an action dispatched at ``t_dispatch`` (perf_counter).

    ``page_t_dispatch`` is ``performance.now()`` on the page clock at dispatch time, used to
    decide whether a mutation/scroll/shift happened *after* the action. If the page navigated
    since, the clock reset; we re-anchor to 0 on that document. ``mutations_at_dispatch`` is the
    observer's cumulative count at dispatch, so the report can state mutations *since* dispatch.
    """
    cfg = cfg or SettleConfig()
    t0 = time.perf_counter()
    deadline = t0 + cfg.timeout_ms / 1000
    quiet_s = cfg.quiet_ms / 1000
    last_busy: dict[str, float] = {}  # signal -> perf_counter when it was last seen busy
    seen_busy_reason: dict[str, str] = {}
    counters: dict[str, Any] = {
        "mutations": 0,
        "bg_mutations": 0,
        "scroll_events": 0,
        "layout_shifts": 0,
        "frames": 0,
        "infinite_animations": 0,
        "bg_animations": 0,
        "timers_peak": 0,
        "timers_max_delay": None,
        "timers_long": [],
        "bg_timers": 0,
        "intervals_started": 0,
    }
    handled_navs = 0
    anchor = page_t_dispatch if page_t_dispatch is not None else 0.0
    mut_base = mutations_at_dispatch
    bg_base = bg_mutations_at_dispatch
    polls = 0
    timed_out = False
    aborted: str | None = None
    busy: list[str] = []
    net_peak = 0

    while True:
        polls += 1
        now = time.perf_counter()
        busy = []

        # --- a page that is gone cannot settle; say so immediately instead of spinning
        if page.is_closed():
            aborted = "page_closed"
            break
        if nav.crashed_at is not None:  # a crashed renderer never comes back
            aborted = "page_crashed"
            break

        # --- navigation: main frame moved since dispatch -> wait for load, re-anchor clock
        navs_since = [u for ts, u in nav.navigations if ts >= t_dispatch]
        if len(navs_since) > handled_navs:
            handled_navs = len(navs_since)
            last_busy["navigation"] = now
            seen_busy_reason["navigation"] = navs_since[-1]
            try:
                await page.wait_for_load_state("load", timeout=cfg.nav_load_timeout_ms)
            except Exception:
                pass
            await ensure_installed(page)
            anchor = 0.0  # new document, new performance clock
            mut_base = bg_base = 0  # ... and a fresh observer
            # the load itself counts as busy; fall through and re-poll

        # --- network
        pending = net.pending_since(t_dispatch, cfg.body_grace_ms / 1000)
        net_peak = max(net_peak, len(pending))
        if pending:
            busy.append("network")
            last_busy["network"] = now
            try:
                seen_busy_reason["network"] = pending[0].url[:200]
            except Exception:
                pass

        # --- in-page signals
        status = None
        context_lost = False
        try:
            status = await page.evaluate(STATUS_JS)
        except Exception as e:
            context_lost = True
            msg = str(e)
            if "crashed" in msg.lower():
                aborted = "page_crashed"
                break
            if page.is_closed() or "closed" in msg.lower():
                aborted = "page_closed"
                break
            busy.append("document")
            last_busy["document"] = now
            seen_busy_reason["document"] = f"evaluate failed: {type(e).__name__}"
        if status is None and not context_lost:
            # __ar missing (page we did not create / navigated before init script) -> install
            await ensure_installed(page)
            busy.append("document")
            last_busy["document"] = now
        elif status is not None:
            pnow = status["now"]
            counters["mutations"] = max(0, status["mutations"] - mut_base)
            counters["bg_mutations"] = max(0, status.get("bgMutations", 0) - bg_base)
            counters["infinite_animations"] = max(
                counters["infinite_animations"], status.get("infiniteAnimations", 0)
            )
            counters["bg_animations"] = max(counters["bg_animations"], status.get("bgAnimations", 0))
            counters["scroll_events"] = status["scrollEvents"]
            counters["layout_shifts"] = status["shifts"]
            counters["frames"] = status.get("frames", 1)
            counters["bg_timers"] = max(counters["bg_timers"], status.get("bgTimers", 0))
            counters["intervals_started"] = max(
                counters["intervals_started"], status.get("intervalsStarted", 0)
            )
            if status["lastMutation"] > anchor and (pnow - status["lastMutation"]) / 1000 < quiet_s:
                busy.append("dom")
                last_busy["dom"] = now
            if status["lastScroll"] > anchor and (pnow - status["lastScroll"]) / 1000 < quiet_s:
                busy.append("scroll")
                last_busy["scroll"] = now
            if status["lastShift"] > anchor and (pnow - status["lastShift"]) / 1000 < quiet_s:
                busy.append("layout")
                last_busy["layout"] = now
            if status["animations"] > 0:
                busy.append("animations")
                last_busy["animations"] = now
                seen_busy_reason["animations"] = f"{status['animations']} running"
            # --- timers the action scheduled: awaited up to timer_wait_ms, reported beyond it
            awaited = [t for t in status.get("pendingTimers", []) if t["delay"] <= cfg.timer_wait_ms]
            long_ = [t["delay"] for t in status.get("pendingTimers", []) if t["delay"] > cfg.timer_wait_ms]
            for dl in long_:
                if (
                    dl not in counters["timers_long"]
                    and len(counters["timers_long"]) < MAX_LONG_TIMERS_LISTED
                ):
                    counters["timers_long"].append(dl)
            if awaited:
                counters["timers_peak"] = max(counters["timers_peak"], len(awaited))
                mx = max(t["delay"] for t in awaited)
                counters["timers_max_delay"] = (
                    mx if counters["timers_max_delay"] is None else max(counters["timers_max_delay"], mx)
                )
                busy.append("timers")
                last_busy["timers"] = now
                remaining = max(0.0, awaited[0]["due"] - pnow)
                seen_busy_reason["timers"] = (
                    f"{len(awaited)} pending setTimeout (delays <= {round(mx)}ms, "
                    f"next due in {round(remaining)}ms)"
                )
            if status["readyState"] != "complete":
                busy.append("document")
                last_busy["document"] = now
                seen_busy_reason["document"] = f"readyState={status['readyState']}"

        # We insist on observing at least one quiet window after dispatch, so that an
        # effect scheduled a few ms after the action is not missed.
        if not busy and (now - t_dispatch) >= quiet_s:
            break
        if now >= deadline:
            timed_out = True
            break
        await asyncio.sleep(cfg.poll_ms / 1000)

    elapsed = (time.perf_counter() - t0) * 1000
    if aborted:
        settled_by = aborted
    elif last_busy:
        latest = max(last_busy.values())
        tied = [k for k, v in last_busy.items() if v == latest]
        settled_by = min(
            tied, key=lambda k: _SETTLED_BY_PRIORITY.index(k) if k in _SETTLED_BY_PRIORITY else 99
        )
    else:
        settled_by = "quiet_window"  # nothing was ever busy; only the quiet window elapsed

    def _until(sig: str):
        return round((last_busy[sig] - t0) * 1000, 1) if sig in last_busy else None

    signals = {
        "network": {
            "peak_pending": net_peak,
            "started_total": net.started,
            "reason": seen_busy_reason.get("network"),
            "busy_until_ms": _until("network"),
            "released": net.released_since(t_dispatch),
        },
        "dom": {
            "mutations": counters["mutations"],
            "background_mutations": counters["bg_mutations"],
            "frames_observed": counters["frames"],
            "busy_until_ms": _until("dom"),
        },
        "scroll": {"events": counters["scroll_events"], "busy_until_ms": _until("scroll")},
        "layout": {"shifts": counters["layout_shifts"], "busy_until_ms": _until("layout")},
        "animations": {
            "reason": seen_busy_reason.get("animations"),
            "busy_until_ms": _until("animations"),
            "infinite": counters["infinite_animations"],
            "background": counters["bg_animations"],
        },
        "timers": {
            "reason": seen_busy_reason.get("timers"),
            "busy_until_ms": _until("timers"),
            "peak_pending": counters["timers_peak"],
            "max_delay_ms": counters["timers_max_delay"],
            "long": counters["timers_long"],
            "background": counters["bg_timers"],
            "intervals_started": counters["intervals_started"],
        },
        "document": {"reason": seen_busy_reason.get("document"), "busy_until_ms": _until("document")},
        "navigation": {"count": handled_navs, "last_url": seen_busy_reason.get("navigation")},
        "quiet_ms": cfg.quiet_ms,
        "timeout_ms": cfg.timeout_ms,
        "body_grace_ms": cfg.body_grace_ms,
        "timer_wait_ms": cfg.timer_wait_ms,
    }
    return SettleReport(
        settled=not timed_out and aborted is None,
        elapsed_ms=elapsed,
        settled_by=settled_by,
        timed_out=timed_out,
        busy_at_timeout=busy if timed_out else [],
        signals=signals,
        polls=polls,
        aborted=aborted,
    )
