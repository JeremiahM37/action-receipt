"""Settlement bookkeeping without a browser: config defaults, report shape, the settled_by
priority order, and the network / navigation trackers driven by fake Playwright events."""

from __future__ import annotations

import asyncio
import time

import pytest

from action_receipt.schema import SettlementModel
from action_receipt.settle import (
    _SETTLED_BY_PRIORITY,
    MAX_HTTP_ERRORS,
    MAX_RELEASED_LISTED,
    SETTLED_BY_VALUES,
    SMALL_BODY_BYTES,
    NavTracker,
    NetworkTracker,
    SettleConfig,
    SettleReport,
)

from ._synth import settle


# ----------------------------------------------------------------------------- fakes
class FakeEmitter:
    """Records ``on(event, handler)`` registrations and lets a test fire them."""

    def __init__(self):
        self.handlers: dict[str, list] = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self.handlers.get(event, []).remove(handler)

    def emit(self, event, *args):
        for h in list(self.handlers.get(event, [])):
            h(*args)


class FakeFrame:
    def __init__(self, url="about:blank"):
        self.url = url


class FakePage(FakeEmitter):
    def __init__(self):
        super().__init__()
        self.main_frame = FakeFrame()


class FakeRequest:
    def __init__(
        self, url="http://127.0.0.1/x", resource_type="fetch", method="GET", navigation=False, frame=None
    ):
        self.url, self.resource_type, self.method = url, resource_type, method
        self._nav, self.frame = navigation, frame

    def is_navigation_request(self):
        return self._nav


class FakeResponse:
    def __init__(self, request, status=200, headers=None):
        self.request, self.status, self.url = request, status, request.url
        self.headers = headers or {}


class FakeDownload:
    def __init__(self, url, filename="f.bin"):
        self.url, self.suggested_filename = url, filename


class FakeDialog:
    def __init__(self, type_="alert", message="hi"):
        self.type, self.message, self.done = type_, message, None

    async def accept(self):
        self.done = "accept"

    async def dismiss(self):
        self.done = "dismiss"


# ----------------------------------------------------------------------------- config / report
def test_settle_config_defaults_match_the_documented_env_defaults():
    c = SettleConfig()
    assert (c.quiet_ms, c.timeout_ms, c.body_grace_ms, c.timer_wait_ms, c.pre_settle_ms) == (
        100.0,
        10000.0,
        1500.0,
        5000.0,
        800.0,
    )
    assert c.poll_ms == 20.0 and c.nav_load_timeout_ms == 10000.0


def test_settle_report_to_dict_rounds_and_matches_the_schema():
    r = settle(elapsed_ms=123.456, settled_by="dom")
    d = r.to_dict()
    assert d["elapsed_ms"] == 123.5 and set(d) == set(SettlementModel.model_fields)
    SettlementModel.model_validate(d)
    assert (
        SettleReport(settled=True, elapsed_ms=1.0, settled_by="quiet_window", timed_out=False).to_dict()[
            "aborted"
        ]
        is None
    )


def test_settled_by_priority_is_navigation_first_and_covers_every_value():
    assert _SETTLED_BY_PRIORITY[0] == "navigation"
    assert (
        _SETTLED_BY_PRIORITY.index("network")
        < _SETTLED_BY_PRIORITY.index("timers")
        < _SETTLED_BY_PRIORITY.index("animations")
    )
    assert (
        _SETTLED_BY_PRIORITY.index("animations")
        < _SETTLED_BY_PRIORITY.index("dom")
        < _SETTLED_BY_PRIORITY.index("layout")
    )
    assert (
        _SETTLED_BY_PRIORITY.index("layout")
        < _SETTLED_BY_PRIORITY.index("scroll")
        < _SETTLED_BY_PRIORITY.index("document")
    )
    assert SETTLED_BY_VALUES == _SETTLED_BY_PRIORITY + ("quiet_window", "page_closed", "page_crashed")
    assert len(set(SETTLED_BY_VALUES)) == len(SETTLED_BY_VALUES)


def test_tie_break_picks_the_highest_priority_signal():
    """The rule ``settle()`` applies when several signals were last busy in the same poll."""
    tied = ["dom", "scroll", "network", "layout"]
    assert (
        min(tied, key=lambda k: _SETTLED_BY_PRIORITY.index(k) if k in _SETTLED_BY_PRIORITY else 99)
        == "network"
    )


# ----------------------------------------------------------------------------- NetworkTracker
def test_network_tracker_counts_and_pending_window():
    page = FakePage()
    net = NetworkTracker(page)
    assert set(page.handlers) == {"request", "requestfinished", "requestfailed", "response", "download"}
    t_before = time.perf_counter()
    old = FakeRequest("http://127.0.0.1/old")
    page.emit("request", old)
    t_dispatch = time.perf_counter()
    new = FakeRequest("http://127.0.0.1/new")
    page.emit("request", new)
    assert net.started == 2
    assert net.pending_since(t_dispatch) == [new]  # the pre-dispatch request is not ours
    assert set(net.pending_since(t_before)) == {old, new}
    page.emit("requestfinished", new)
    assert net.pending_since(t_dispatch) == [] and net.finished == 1
    page.emit("requestfailed", old)
    assert net.failed == 1 and net.inflight == {}


def test_streaming_requests_never_keep_the_page_busy():
    page = FakePage()
    net = NetworkTracker(page)
    t = time.perf_counter()
    for rtype in ("websocket", "eventsource", "media"):
        page.emit("request", FakeRequest(resource_type=rtype))
    page.emit("request", FakeRequest(resource_type="xhr"))
    assert [r.resource_type for r in net.pending_since(t)] == ["xhr"]


def test_small_body_is_released_on_headers_and_large_body_after_grace():
    page = FakePage()
    net = NetworkTracker(page)
    t = time.perf_counter()
    small, big = FakeRequest("http://127.0.0.1/small"), FakeRequest("http://127.0.0.1/big")
    page.emit("request", small)
    page.emit("request", big)
    page.emit("response", FakeResponse(small, headers={"content-length": str(SMALL_BODY_BYTES)}))
    page.emit("response", FakeResponse(big, headers={"content-length": str(SMALL_BODY_BYTES + 1)}))
    assert net.pending_since(t, body_grace_s=10.0) == [big]
    rel = net.released_since(t)
    assert len(rel) == 1 and rel[0]["url"].endswith("/small") and "already buffered" in rel[0]["reason"]
    # the grace elapses -> released with the other reason
    assert net.pending_since(t, body_grace_s=0.0) == []
    rel = net.released_since(t)
    assert (
        len(rel) == 2
        and rel[1]["url"].endswith("/big")
        and "body not finished within 0ms" in rel[1]["reason"]
    )
    # without a grace value the answered-but-open response keeps waiting
    page.emit("request", big2 := FakeRequest("http://127.0.0.1/big2"))
    page.emit("response", FakeResponse(big2, headers={}))
    assert net.pending_since(t) == [big2]


def test_http_errors_are_recorded_and_capped():
    page = FakePage()
    net = NetworkTracker(page)
    t = time.perf_counter()
    for i in range(MAX_HTTP_ERRORS + 3):
        page.emit(
            "response",
            FakeResponse(FakeRequest(f"http://127.0.0.1/e{i}", method="POST"), status=500 if i % 2 else 404),
        )
    page.emit("response", FakeResponse(FakeRequest("http://127.0.0.1/ok"), status=200))
    errs = net.http_errors_since(t)
    assert len(errs) == MAX_HTTP_ERRORS
    assert errs[0] == {"url": "http://127.0.0.1/e0", "status": 404, "method": "POST"}
    assert net.http_errors_since(time.perf_counter() + 1) == []


def test_released_list_is_capped_in_the_report():
    page = FakePage()
    net = NetworkTracker(page)
    t = time.perf_counter()
    for i in range(MAX_RELEASED_LISTED + 2):
        r = FakeRequest(f"http://127.0.0.1/s{i}")
        page.emit("request", r)
        page.emit("response", FakeResponse(r, headers={"content-length": "10"}))
    assert len(net.released_since(t)) == MAX_RELEASED_LISTED


def test_download_releases_the_request_that_became_it():
    page = FakePage()
    net = NetworkTracker(page)
    t = time.perf_counter()
    r = FakeRequest("http://127.0.0.1/file.bin")
    page.emit("request", r)
    page.emit("download", FakeDownload("http://127.0.0.1/file.bin"))
    assert net.pending_since(t) == []


# ----------------------------------------------------------------------------- NavTracker
def test_nav_tracker_records_main_frame_navigations_and_document_requests():
    page, ctx = FakePage(), FakeEmitter()
    nav = NavTracker(page, ctx)
    t = time.perf_counter()
    main = page.main_frame
    main.url = "http://127.0.0.1/a"
    page.emit("framenavigated", main)
    page.emit("framenavigated", FakeFrame("http://127.0.0.1/child"))  # a child frame: ignored
    page.emit("request", FakeRequest("http://127.0.0.1/a", navigation=True, frame=main))
    page.emit("request", FakeRequest("http://127.0.0.1/a.css", navigation=False, frame=main))
    page.emit("request", FakeRequest("http://127.0.0.1/sub", navigation=True, frame=object()))
    ev = nav.since(t)
    assert ev["navigations"] == ["http://127.0.0.1/a"]
    assert ev["cross_document_navigations"] == ["http://127.0.0.1/a"]
    assert ev["new_pages"] == [] and not ev["crashed"]


def test_document_request_without_a_navigation_event_is_not_cross_document():
    page, ctx = FakePage(), FakeEmitter()
    nav = NavTracker(page, ctx)
    t = time.perf_counter()
    page.emit("request", FakeRequest("http://127.0.0.1/a", navigation=True, frame=page.main_frame))
    assert nav.since(t)["cross_document_navigations"] == []


def test_nav_tracker_console_download_crash_and_new_pages():
    page, ctx = FakePage(), FakeEmitter()
    nav = NavTracker(page, ctx)
    t = time.perf_counter()

    class Msg:
        def __init__(self, type_, text):
            self.type, self.text = type_, text

    page.emit("console", Msg("error", "boom"))
    page.emit("console", Msg("log", "fine"))
    page.emit("pageerror", RuntimeError("thrown"))
    page.emit("download", FakeDownload("http://127.0.0.1/f", "f.bin"))
    new_page = object()
    ctx.emit("page", new_page)
    page.emit("crash")
    ev = nav.since(t)
    assert ev["console_errors"] == ["boom", "pageerror: thrown"]
    assert ev["downloads"] == [{"filename": "f.bin", "url": "http://127.0.0.1/f"}]
    assert ev["new_pages"] == [new_page] and ev["crashed"] and nav.crashed_at is not None


def test_nav_tracker_stops_listening_for_pages_when_its_page_closes():
    page, ctx = FakePage(), FakeEmitter()
    nav = NavTracker(page, ctx)
    page.emit("close")
    ctx.emit("page", object())
    assert nav.new_pages == []


@pytest.mark.parametrize("action", ["accept", "dismiss"])
async def test_dialogs_are_handled_per_the_configured_action(action):
    page, ctx = FakePage(), FakeEmitter()
    nav = NavTracker(page, ctx)
    nav.dialog_action = action
    t = time.perf_counter()
    dlg = FakeDialog("confirm", "sure?")
    page.emit("dialog", dlg)
    await asyncio.sleep(0)
    assert dlg.done == action
    assert nav.since(t)["dialogs"] == [{"type": "confirm", "message": "sure?", "handled": action}]
