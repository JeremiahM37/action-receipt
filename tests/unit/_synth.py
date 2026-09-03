"""Synthetic building blocks for the unit tier: captures, settlement reports, deltas and whole
receipts with the same shape the browser path produces, but no browser."""

from __future__ import annotations

from typing import Any

from PIL import Image

from action_receipt.capture import Capture, Screenshot, dhash
from action_receipt.delta import Delta
from action_receipt.session import Receipt
from action_receipt.settle import SettleReport
from action_receipt.verdict import Verdict, decide

URL = "http://127.0.0.1:1/page.html"

SCROLL = {
    "x": 0,
    "y": 0,
    "scrollHeight": 800,
    "clientHeight": 600,
    "pageScrollable": True,
    "overflowHidden": False,
    "atTop": True,
    "atBottom": False,
}


def signals(**over: Any) -> dict[str, Any]:
    """A complete ``settlement.signals`` block (the shape ``settle()`` emits); ``over`` patches
    top-level keys."""
    s: dict[str, Any] = {
        "network": {
            "peak_pending": 0,
            "started_total": 0,
            "reason": None,
            "busy_until_ms": None,
            "released": [],
        },
        "dom": {"mutations": 0, "background_mutations": 0, "frames_observed": 1, "busy_until_ms": None},
        "scroll": {"events": 0, "busy_until_ms": None},
        "layout": {"shifts": 0, "busy_until_ms": None},
        "animations": {"reason": None, "busy_until_ms": None, "infinite": 0, "background": 0},
        "timers": {
            "reason": None,
            "busy_until_ms": None,
            "peak_pending": 0,
            "max_delay_ms": None,
            "long": [],
            "background": 0,
            "intervals_started": 0,
        },
        "document": {"reason": None, "busy_until_ms": None},
        "navigation": {"count": 0, "last_url": None},
        "quiet_ms": 100.0,
        "timeout_ms": 8000.0,
        "body_grace_ms": 1500.0,
        "timer_wait_ms": 5000.0,
    }
    s.update(over)
    return s


def settle(
    *,
    settled_by: str = "quiet_window",
    elapsed_ms: float = 120.0,
    timed_out: bool = False,
    busy: list[str] | None = None,
    aborted: str | None = None,
    polls: int = 6,
    **sig: Any,
) -> SettleReport:
    return SettleReport(
        settled=not timed_out and aborted is None,
        elapsed_ms=elapsed_ms,
        settled_by=settled_by,
        timed_out=timed_out,
        busy_at_timeout=busy or [],
        signals=signals(**sig),
        polls=polls,
        aborted=aborted,
    )


def image(w: int = 160, h: int = 96, colour: int = 255) -> Image.Image:
    return Image.new("L", (w, h), colour)


def screenshot(img: Image.Image | None = None, *, width: int = 1000, height: int = 600) -> Screenshot:
    img = img or image()
    return Screenshot(dhash=dhash(img), width=width, height=height, small=img.convert("L"))


def capture(
    *,
    url: str = URL,
    title: str = "Page",
    nodes: dict[str, str] | None = None,
    scroll: dict | None = None,
    focused: dict | None = None,
    modals: list[str] | None = None,
    target: dict | None = None,
    frames: list[dict] | None = None,
    background: list[str] | None = None,
    background_paths: set[str] | None = None,
    background_rects: list[list[int]] | None = None,
    shot: Screenshot | None = None,
    truncated: bool = False,
    element_total: int | None = None,
    containers: list[dict] | None = None,
    ok: bool = True,
    text_hash: int | None = None,
    **extra: Any,
) -> Capture:
    nodes = dict(
        nodes if nodes is not None else {"html:nth-of-type(1)": "html", "body:nth-of-type(1)": "body"}
    )
    c = Capture(
        ok=ok,
        ts=0.0,
        elapsed_ms=5.0,
        url=url,
        title=title,
        ready_state="complete",
        viewport={"width": 1000, "height": 600},
        scroll=dict(scroll or SCROLL),
        focused=focused,
        modals=list(modals or []),
        scrollable_containers=list(containers or []),
        frames=list(frames or []),
        element_count=len(nodes),
        element_total=element_total if element_total is not None else len(nodes),
        truncated=truncated,
        text_hash=text_hash if text_hash is not None else hash(tuple(sorted(nodes.values()))) & 0xFFFF,
        text_length=sum(len(v) for v in nodes.values()),
        nodes=nodes,
        background=list(background or []),
        background_paths=set(background_paths or ()),
        background_rects=list(background_rects or []),
        dom_hash=("%016x" % (hash(tuple(sorted(nodes.items()))) & 0xFFFFFFFFFFFFFFFF)),
        target=target,
        screenshot=shot,
    )
    for k, v in extra.items():
        setattr(c, k, v)
    return c


def delta(**kw: Any) -> Delta:
    d = Delta(url_before=URL, url_after=URL)
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def verdict_for(
    d: Delta,
    *,
    action: str = "click",
    args: dict | None = None,
    dispatch_error: str | None = None,
    settlement: SettleReport | None = None,
    before_scroll: dict | None = None,
    containers: list[dict] | None = None,
    target: dict | None = None,
    frames: list[dict] | None = None,
) -> Verdict:
    return decide(
        d,
        action=action,
        args=args if args is not None else {"selector": "#x"},
        dispatch_error=dispatch_error,
        settlement=settlement or settle(),
        before_scroll=before_scroll if before_scroll is not None else dict(SCROLL),
        containers=containers or [],
        target_before=target,
        frames=frames,
    )


def receipt(
    *,
    action: str = "click",
    args: dict | None = None,
    d: Delta | None = None,
    v: Verdict | None = None,
    settlement: SettleReport | None = None,
    before: Capture | None = None,
    after: Capture | None = None,
    dispatch: dict | None = None,
    timing: dict | None = None,
) -> Receipt:
    """A whole receipt of the shape ``ReceiptSession.run`` builds; defaults make a valid ``no_op``."""
    d = d or delta()
    v = v or verdict_for(d, action=action)
    before = before or capture()
    after = after or capture()
    dispatch = dispatch or {
        "ok": True,
        "error": None,
        "elapsed_ms": 12.0,
        "preflight": before.target,
        "pre_settle": {
            "elapsed_ms": 0.4,
            "polls": 1,
            "quiet": True,
            "waited_for": None,
            "background_roots": 0,
            "max_ms": 800.0,
        },
    }
    timing = timing or {
        "prescroll": 0.1,
        "pre_settle": 0.4,
        "capture_before": 5.0,
        "dispatch": 12.0,
        "settle": 120.0,
        "capture_after": 5.0,
        "observed_window": 140.0,
        "total": 150.0,
    }
    return Receipt(
        id="deadbeef0123",
        action=action,
        args=args if args is not None else {"selector": "#x"},
        verdict=v,
        dispatch=dispatch,
        settlement=settlement or settle(),
        delta=d,
        before=before,
        after=after,
        timing=timing,
    )
